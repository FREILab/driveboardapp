/*
 * Generic simavr harness for DriveboardFirmware safety tests.
 *
 * Loads a firmware ELF into a simulated atmega328p and lets the caller:
 *   - drive input pins HIGH on PORTC (limit switches) and PORTD (door/chiller),
 *   - inject raw bytes on the UART RX line (one at a time, spaced out),
 *   - idle for a while before sending (to exercise the serial watchdog),
 * then prints every byte the firmware transmitted on UART0.
 *
 * The duplicate-transmission protocol (each command byte sent twice) is NOT
 * applied here — the Python caller builds the exact byte stream it wants,
 * which keeps this harness a dumb, general transport.
 *
 * Usage:
 *   simavr_runner <firmware.elf> [--portc=B,B] [--portd=B,B]
 *                 [--send=N,N,...] [--idle-cycles=N] [--run-cycles=N]
 *                 [--portc-delay=N] [--watch-symbol=NAME [--watch-size=W]]
 *                 [--count-portb=BIT] [--watch-pin=P,BIT]
 *                 [--fork-at=K --fork-count=N [--fork-step=S]]
 *
 * Every time option is in AVR clock cycles (avr->cycle, 16 per microsecond),
 * never in simulator steps, so test timing does not shift with the firmware's
 * instruction mix. --run-cycles counts from reset, --idle-cycles and
 * --portc-delay from the boot hello. --send bytes arrive one per
 * RX_GAP_CYCLES, a host's 57600 baud 8N1 wire, the first BOOT_CYCLES +
 * --idle-cycles after the hello.
 *
 * --fork-at=K --fork-count=N [--fork-step=S] sweeps timing without paying for
 * boot and setup N times: when byte K of --send is due, the harness forks N
 * children and child j sends byte K onward j*S cycles later. Each child prints
 * FORK=<j> and then its own output block; the parent prints nothing.
 *
 * --watch-symbol samples an SRAM symbol (e.g. pwm_duty) after every
 * instruction and reports the maximum and final value seen, so internal
 * safety state (like the laser duty being forced to 0) can be asserted.
 *
 * simavr starts a timer counting from 0, ignoring a TCNT written while the
 * timer was stopped. The firmware primes TCNT2 that way for its ~3us step
 * reset pulse, so the harness replays the primed TCNT2 when timer2 starts;
 * without that the pulse is a full 256-tick overflow (128us) wide and a fast
 * move loses step edges.
 *
 * simavr's UART lands a raised RX byte one frame later, and counts that frame
 * as 11 bits (start, 8 data, parity, stop) though the firmware runs 8N1. The
 * harness gives each frame its 10 bits, and lands every --send byte at the
 * cycle it is due: left to simavr's frame delay, a byte raised while the one
 * before is still unread waits on the firmware's rx ISR, so its arrival would
 * follow the firmware's timing instead of the wire's.
 *
 * Output (stdout):
 *   OUT: <decimal bytes the firmware transmitted, space separated>
 *   HELLO=<0|1>
 *   SYM <name> max=<n> final=<n>   (only if --watch-symbol given)
 *
 * Built and driven by backend/tests/firmware_sim.py; needs libsimavr-dev.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#include "sim_avr.h"
#include "sim_elf.h"
#include "avr_uart.h"
#include "avr_ioport.h"
#include "sim_irq.h"

#define INFO_HELLO 0x7E /* '~' emitted by serial_init() on boot */
#define CAP_MAX 65536
#define RX_GAP_CYCLES 2778 /* 16MHz * 10 bits / 57600 baud */
/* The firmware sends its hello before it finishes initialising, and bytes that
 * land in the ~0.6ms until then are lost. A host only starts talking ms after
 * it reads the hello, so the first --send byte waits this long (1ms). */
#define BOOT_CYCLES 16000
#define MAX_SEND 16384 /* as --send=, ~4 chars a byte, stays under the 128KB argv string cap */
#define MAX_PINS 16
/* atmega328p data-space addresses of the timer2 registers */
#define TCCR2B_ADDR 0xB1
#define TCNT2_ADDR 0xB2

static uint8_t cap[CAP_MAX];
static int cap_len = 0;
static int got_hello = 0;

static long step_edges = 0; /* rising edges on the watched step pin */
static int step_last = 0;

static void uart_tx_hook(struct avr_irq_t *irq, uint32_t value, void *param) {
    uint8_t byte = value & 0xff;
    if (byte == INFO_HELLO)
        got_hello = 1;
    if (cap_len < CAP_MAX)
        cap[cap_len++] = byte;
}

static void step_pin_hook(struct avr_irq_t *irq, uint32_t value, void *param) {
    int lvl = value & 1;
    if (lvl && !step_last)
        step_edges++;
    step_last = lvl;
}

/* Level of a watched output pin: whether it was ever driven high, and where it
 * was left. Used for the assist relays, which have no SRAM state of their own. */
static int pin_ever_high = 0, pin_final = 0;

static void level_pin_hook(struct avr_irq_t *irq, uint32_t value, void *param) {
    pin_final = value & 1;
    if (pin_final)
        pin_ever_high = 1;
}

static avr_uart_t *uart0(avr_t *avr) {
    for (avr_io_t *io = avr->io_port; io; io = io->next)
        if (!strcmp(io->kind, "uart") && ((avr_uart_t *)io)->name == '0')
            return (avr_uart_t *)io;
    return NULL;
}

/* Runs after simavr's own TCCR2B handler has (re)started timer2 from 0: on a
 * start from stopped, write the TCNT2 the firmware primed back through
 * simavr's TCNT2 handler so the overflow comes when it would on silicon. */
static void tccr2b_hook(struct avr_t *avr, avr_io_addr_t addr, uint8_t v, void *param) {
    static int running = 0;
    int start = (v & 7) && !running;
    running = v & 7;
    if (!start)
        return;
    int io = AVR_DATA_TO_IO(TCNT2_ADDR);
    avr->io[io].w.c(avr, TCNT2_ADDR, avr->data[TCNT2_ADDR], avr->io[io].w.param);
}

/* Returns the count parsed, or -1 if there are more than max values (a
 * silently truncated --send would quietly test a shorter stream). */
static int parse_ints(const char *s, int *out, int max) {
    int n = 0;
    while (s && *s) {
        if (n == max)
            return -1;
        out[n++] = (int)strtol(s, (char **)&s, 10);
        if (*s == ',')
            s++;
        else
            break;
    }
    return n;
}

static const char *opt_val(const char *arg, const char *key) {
    size_t klen = strlen(key);
    if (strncmp(arg, key, klen) == 0)
        return arg + klen;
    return NULL;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <firmware.elf> [opts]\n", argv[0]);
        return 2;
    }

    int portc_bits[MAX_PINS], portd_bits[MAX_PINS];
    int n_portc = 0, n_portd = 0;
    int send_bytes[MAX_SEND];
    int n_send = 0;
    long idle_cycles = 0;
    long run_cycles = 40000000L; /* ~2.5s at 16 MHz */
    const char *watch_name = NULL;
    int count_portb_bit = -1; /* count rising edges on this PORTB pin (step pin) */
    long portc_delay = 0;     /* apply PORTC pins this many cycles after hello */
    char watch_pin_port = 0;  /* watch the level of this output pin, e.g. D,4 */
    int watch_size_opt = 0;   /* byte width of the watched symbol, default 1 */
    int watch_pin_bit = -1;
    long fork_at = -1, fork_count = 0, fork_step = 1;

    for (int i = 2; i < argc; i++) {
        const char *v;
        if ((v = opt_val(argv[i], "--portc=")))
            n_portc = parse_ints(v, portc_bits, MAX_PINS);
        else if ((v = opt_val(argv[i], "--portd=")))
            n_portd = parse_ints(v, portd_bits, MAX_PINS);
        else if ((v = opt_val(argv[i], "--send=")))
            n_send = parse_ints(v, send_bytes, MAX_SEND);
        else if ((v = opt_val(argv[i], "--idle-cycles=")))
            idle_cycles = strtol(v, NULL, 10);
        else if ((v = opt_val(argv[i], "--run-cycles=")))
            run_cycles = strtol(v, NULL, 10);
        else if ((v = opt_val(argv[i], "--watch-symbol=")))
            watch_name = v;
        else if ((v = opt_val(argv[i], "--watch-size=")))
            watch_size_opt = (int)strtol(v, NULL, 10);
        else if ((v = opt_val(argv[i], "--count-portb=")))
            count_portb_bit = (int)strtol(v, NULL, 10);
        else if ((v = opt_val(argv[i], "--portc-delay=")))
            portc_delay = strtol(v, NULL, 10);
        else if ((v = opt_val(argv[i], "--fork-at=")))
            fork_at = strtol(v, NULL, 10);
        else if ((v = opt_val(argv[i], "--fork-count=")))
            fork_count = strtol(v, NULL, 10);
        else if ((v = opt_val(argv[i], "--fork-step=")))
            fork_step = strtol(v, NULL, 10);
        else if ((v = opt_val(argv[i], "--watch-pin="))) {
            /* form: <port letter>,<bit> e.g. D,4 */
            watch_pin_port = v[0];
            watch_pin_bit = (v[1] == ',') ? (int)strtol(v + 2, NULL, 10) : -1;
        }
        else
            fprintf(stderr, "WARN: ignoring unknown arg %s\n", argv[i]);
    }

    if (n_portc < 0 || n_portd < 0 || n_send < 0) {
        fprintf(stderr, "ERROR: too many values (max %d pins, %d send bytes)\n",
                MAX_PINS, MAX_SEND);
        return 2;
    }

    elf_firmware_t f;
    memset(&f, 0, sizeof(f));
    if (elf_read_firmware(argv[1], &f) != 0) {
        fprintf(stderr, "ERROR: cannot read ELF %s\n", argv[1]);
        return 2;
    }

    avr_t *avr = avr_make_mcu_by_name("atmega328p");
    if (!avr)
        avr = avr_make_mcu_by_name("atmega328");
    if (!avr) {
        fprintf(stderr, "ERROR: atmega328p core not in simavr\n");
        return 2;
    }
    avr_init(avr);
    avr->frequency = 16000000;
    avr_load_firmware(avr, &f);

    avr_irq_t *uart_tx = avr_io_getirq(avr, AVR_IOCTL_UART_GETIRQ('0'), UART_IRQ_OUTPUT);
    avr_irq_t *uart_rx = avr_io_getirq(avr, AVR_IOCTL_UART_GETIRQ('0'), UART_IRQ_INPUT);
    avr_uart_t *uart = uart0(avr);
    if (!uart_tx || !uart_rx || !uart) {
        fprintf(stderr, "ERROR: no UART0 irqs\n");
        return 2;
    }
    avr_irq_register_notify(uart_tx, uart_tx_hook, NULL);
    /* simavr echoes UART output to stderr once its line buffer fills; the
     * binary protocol is not UTF-8 and the caller decodes stderr as text. */
    uint32_t uart_flags = 0;
    avr_ioctl(avr, AVR_IOCTL_UART_GET_FLAGS('0'), &uart_flags);
    uart_flags &= ~AVR_UART_FLAG_STDIO;
    avr_ioctl(avr, AVR_IOCTL_UART_SET_FLAGS('0'), &uart_flags);

    avr_register_io_write(avr, TCCR2B_ADDR, tccr2b_hook, NULL);

    if (count_portb_bit >= 0)
        avr_irq_register_notify(
            avr_io_getirq(avr, AVR_IOCTL_IOPORT_GETIRQ('B'), count_portb_bit),
            step_pin_hook, NULL);

    if (watch_pin_bit >= 0)
        avr_irq_register_notify(
            avr_io_getirq(avr, AVR_IOCTL_IOPORT_GETIRQ(watch_pin_port), watch_pin_bit),
            level_pin_hook, NULL);

    /* Resolve a watched SRAM symbol (e.g. pwm_duty) to its data-space address. */
    int watch_addr = -1;
    long watch_max = 0, watch_final = 0;
    /* Width of the watched variable. A uint8_t reads as one byte, but a
     * uint32_t like adjusted_rate needs all four or only its low byte shows. */
    int watch_width = 1;
    if (watch_name) {
        for (uint32_t s = 0; s < f.symbolcount; s++) {
            if (strcmp(f.symbol[s]->symbol, watch_name) == 0) {
                watch_addr = f.symbol[s]->addr & 0xffff; /* strip 0x800000 data offset */
                if (watch_size_opt > 0)
                    watch_width = watch_size_opt;
                break;
            }
        }
        if (watch_addr < 0) {
            fprintf(stderr, "ERROR: symbol %s not found\n", watch_name);
            return 2;
        }
    }

    int portd_applied = 0;
    int portc_applied = 0;
    int send_idx = 0;
    long next_send_cycle = -1;
    long hello_cycle = -1;

    while ((long)avr->cycle < run_cycles) {
        int state = avr_run(avr);
        if (state == cpu_Done || state == cpu_Crashed) {
            fprintf(stderr, "ERROR: cpu stopped (state %d)\n", state);
            break;
        }
        if (watch_addr >= 0) {
            long v = 0;
            for (int b = 0; b < watch_width; b++)
                v |= (long)avr->data[watch_addr + b] << (8 * b);
            if (v > watch_max)
                watch_max = v;
            watch_final = v;
        }
        if (!got_hello)
            continue;
        long i = (long)avr->cycle;
        if (hello_cycle < 0) {
            hello_cycle = i;
            next_send_cycle = i + BOOT_CYCLES + idle_cycles;
            uart->cycles_per_byte = uart->cycles_per_byte * 10 / 11; /* 8N1 */
        }
        /* PORTD (door/chiller) pins applied right after boot. */
        if (!portd_applied) {
            for (int p = 0; p < n_portd; p++)
                avr_raise_irq(
                    avr_io_getirq(avr, AVR_IOCTL_IOPORT_GETIRQ('D'), portd_bits[p]), 1);
            portd_applied = 1;
        }
        /* PORTC (limit) pins applied after the optional delay, so a limit can
         * be tripped mid-move rather than only at boot. */
        if (!portc_applied && i >= hello_cycle + portc_delay) {
            for (int p = 0; p < n_portc; p++)
                avr_raise_irq(
                    avr_io_getirq(avr, AVR_IOCTL_IOPORT_GETIRQ('C'), portc_bits[p]), 1);
            portc_applied = 1;
        }
        if (fork_count > 0 && send_idx == fork_at && i >= next_send_cycle) {
            fflush(stdout);
            long j = 0;
            for (; j < fork_count; j++) {
                pid_t pid = fork();
                if (pid == 0)
                    break;
                if (pid < 0 || waitpid(pid, NULL, 0) < 0)
                    return 2;
            }
            if (j == fork_count)
                return 0; /* parent: the children have reported */
            printf("FORK=%ld\n", j);
            next_send_cycle += j * fork_step;
            fork_at = -1;
        }
        /* Feed the raw byte stream, one byte per RX_GAP_CYCLES, each landing
         * now rather than a simavr frame from now. */
        if (send_idx < n_send && i >= next_send_cycle) {
            avr_cycle_count_t frame = uart->cycles_per_byte;
            uart->cycles_per_byte = 1;
            avr_raise_irq(uart_rx, send_bytes[send_idx] & 0xff);
            uart->cycles_per_byte = frame;
            send_idx++;
            next_send_cycle += RX_GAP_CYCLES;
        }
    }

    printf("OUT:");
    for (int i = 0; i < cap_len; i++)
        printf(" %d", cap[i]);
    printf("\n");
    printf("HELLO=%d\n", got_hello);
    if (watch_name)
        printf("SYM %s max=%ld final=%ld\n", watch_name, watch_max, watch_final);
    if (count_portb_bit >= 0)
        printf("STEPS=%ld\n", step_edges);
    if (watch_pin_bit >= 0)
        printf("PIN %c%d everhigh=%d final=%d\n", watch_pin_port, watch_pin_bit,
               pin_ever_high, pin_final);
    return 0;
}
