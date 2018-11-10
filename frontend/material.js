
var server_address = 'http://192.168.178.113'

var chosen_material = undefined
var chosen_max_quantity = 0
var chosen_quantity = 0
var materials = undefined
var current_user = undefined

function controls_material_ready() {

  $("#material_btn").click(function(e){
    $('#material_modal').modal('toggle')
    return false
  })

  Mousetrap.bind(['t'], function(e) {
      $('#material_btn').trigger('click')
      return false;
  })
  
  update_materials()
  update_current_user()
  window.setInterval(update_current_user, 3000);
  
}

function chose_material(id) {
  chosen_material = materials[id]
  
  if (chosen_material.name == "Leer") {
    return
  }
  
  $('#material_modal').modal('toggle')
  
  if (chosen_material.name == "Restekiste") {
    $('#restekiste_spende')[0].value = (chosen_material.price_recommendation/100) + " €"
    $('#restekiste_modal').modal('toggle')
    return
  }
  if (chosen_material.name == "Abgesprochenes Material") {
    $('#abgesprochen_material')[0].value = ""
    $('#abgesprochen_spende')[0].value = "0,00 €"
    $('#abgesprochen_modal').modal('toggle')
    return
  }
  if (chosen_material.name == "Bereits abgerechnet") {
    $('#wiederverwendet_modal').modal('toggle')
    return
  }
  var chosen_materials = document.getElementsByClassName('chosen_material')
  for (var i = 0; i < chosen_materials.length; i++) {
    chosen_materials[i].innerHTML = chosen_material.name
  }
  chosen_max_quantity = chosen_material.size;
  chose_quantity(0);
  if (chosen_max_quantity == 3) {
    var material_quantities = document.getElementsByClassName('material_quantity_3')
    for (var i = 0; i < 2; i++) {
      material_quantities[i].innerHTML = '<p>' + (i+1) + '/3<br>' + (chosen_material.price_part*(i+1)/100).toFixed(2) + ' €</p>'
    }
    material_quantities[2].innerHTML = '<p>1<br>' + (chosen_material.price_full/100).toFixed(2) + ' €</p>'
    $('#material_quantity_3_modal').modal('toggle')
  } else {
    var material_quantities = document.getElementsByClassName('material_quantity_6')
    for (var i = 0; i < 5; i++) {
      material_quantities[i].innerHTML = '<p>' + (i+1) + '/6<br>' + (chosen_material.price_part*(i+1)/100).toFixed(2) + ' €</p>'
    }
    material_quantities[5].innerHTML = '<p>1<br>' + (chosen_material.price_full/100).toFixed(2) + ' €</p>'
    $('#material_quantity_6_modal').modal('toggle')
  }  
}

function chose_quantity(quantity) {
  chosen_quantity = quantity
  var material_quantyties = undefined
  if (chosen_max_quantity == 3) {
    material_quantyties = document.getElementsByClassName("material_quantity_3")
  } else {
    material_quantyties = document.getElementsByClassName("material_quantity_6")
  }
  for (var i = 0; i < material_quantyties.length; i++) {
    if (material_quantyties[i].getAttribute('quantity') == quantity) {
      material_quantyties[i].setAttribute('style','border-color: blue; color: blue; background-color: #DEDEDE;')
    } else {
      material_quantyties[i].setAttribute('style','border-color: black; color: black; background-color: white;')
    }
  }
  var run_btns = document.getElementsByClassName('run_btn')
  for (var i = 0; i < run_btns.length; i++) {
    if (quantity == 0){
      run_btns[i].disabled = true;
    } else {
      run_btns[i].disabled = false;
    }
  }
}

function update_materials() {
  $.get(server_address + '/lasercutter_materials', function(data, status){
    
    if (status != 'success') {
      return
    }
    
    materials = data
    /*
    materials = JSON.parse(`[
    {"name": "MDF 3mm01", "icon": "/img/material/mdf_3mm.png", "price_full": 300, "size": "3", "price_part": 100}, 
    {"name": "MDF 5mm02", "icon": "/img/material/mdf_5mm.png", "price_full": 150, "size": "6", "price_part": 50},
    {"name": "MDF 3mm03", "icon": "/img/material/mdf_3mm.png", "price_full": 300, "size": "3", "price_part": 100}, 
    {"name": "MDF 5mm04", "icon": "/img/material/mdf_5mm.png", "price_full": 150, "size": "6", "price_part": 50},
    {"name": "MDF 3mm05", "icon": "/img/material/mdf_3mm.png", "price_full": 300, "size": "3", "price_part": 100}, 
    {"name": "MDF 5mm06", "icon": "/img/material/mdf_5mm.png", "price_full": 150, "size": "6", "price_part": 50},
    {"name": "MDF 3mm07", "icon": "/img/material/mdf_3mm.png", "price_full": 300, "size": "3", "price_part": 100}, 
    {"name": "MDF 5mm08", "icon": "/img/material/mdf_5mm.png", "price_full": 150, "size": "6", "price_part": 50},
    {"name": "MDF 3mm09", "icon": "/img/material/mdf_3mm.png", "price_full": 300, "size": "3", "price_part": 100}, 
    {"name": "MDF 5mm10", "icon": "/img/material/mdf_5mm.png", "price_full": 150, "size": "6", "price_part": 50},
    {"name": "MDF 3mm11", "icon": "/img/material/mdf_3mm.png", "price_full": 300, "size": "3", "price_part": 100}, 
    {"name": "MDF 5mm12", "icon": "/img/material/mdf_5mm.png", "price_full": 150, "size": "6", "price_part": 50},
    {"name": "MDF 3mm13", "icon": "/img/material/mdf_3mm.png", "price_full": 300, "size": "3", "price_part": 100}, 
    {"name": "MDF 5mm14", "icon": "/img/material/mdf_5mm.png", "price_full": 150, "size": "6", "price_part": 50},
    {"name": "Leer", "icon": "/img/material/empty.png"}, 
    {"name": "Restekiste", "icon": "/img/material/Restekiste.png", "price_recommendation": 20},
    {"name": "Bereits abgerechnet", "icon": "/img/material/bereitsBerechnet.png"}, 
    {"name": "Abgesprochenes Material", "icon": "/img/material/zugelassen.png"}
    ]`)
    */
    
    var html = '<tr>'  
    for (var i = 0; i < materials.length; i++) {
      html += '<td class="material"><div>'
      html += '<img src="' + materials[i].icon + '" width="125" height="125" '
      html += 'onclick="chose_material(\'' + i + '\')">'
      html += '</div></td>'    
      if (i % 6 == 5 && i + 1 != materials.length) {
        html += '</tr><tr>'
      }
    }   
    html += '</tr>'
    $('#material_table')[0].innerHTML = html
  });
}

function update_current_user() {
  $.get(server_address + '/last_user_at_lasercutter/21042017freilab1337fooboardasgeht111', function(data, status){
    
    if (status != 'success') {
      return
    }
    
    // data = JSON.parse('{"Mitgliedsname": "Max Musterman", "Guthaben": 3000}');
    
    if ( 'Mitgliedsname' in data) {
      current_user = data
      var balance = data.Guthaben
      var name = data.Mitgliedsname
      $('#user_name')[0].innerHTML = name;
      $('#user_balance')[0].innerHTML = (balance/100).toFixed(2) + ' €';
      if (balance < 0) {
        $('#user_balance')[0].style.color = 'red'
      } else {
        $('#user_balance')[0].style.color = 'black'
      }
    } else {
      current_user = undefined
      $('#user_name')[0].innerHTML = '';
      $('#user_balance')[0].innerHTML = '';
    }
  });
}

function bill_material_usage() {
  var data = {'Mitgliedsname':current_user.Mitgliedsname}
  
  if (chosen_material.name == "Restekiste") {
    data.Restekiste = true
    amount = $('#restekiste_spende')[0].value.replace('€','').replace(',','.');
    if (!isNaN(amount)) {
      if (parseFloat(amount) >= 0) {
        data.Betrag = - Math.round(parseFloat(amount) * 100)
      } else {
        $().uxmessage('error', 'Spendenbetrag muss positiv sein.')
        return
      }
    } else {
      $().uxmessage('error', 'Spendenbetrag muss Zahl sein.')
      return
    }
  } else if (chosen_material.name == "Abgesprochenes Material") {
    material_kind = $('#abgesprochen_material')[0].value
    if (material_kind != "") {
      data.AbgesprochenesMaterial = $('#abgesprochen_material')[0].value
    } else {
      $().uxmessage('error', 'Art des Materials fehlt.')
      return
    }    
    amount = $('#abgesprochen_spende')[0].value.replace('€','').replace(',','.');
    if (!isNaN(amount)) {
      if (parseFloat(amount) > 0) {
        data.Betrag = - Math.round(parseFloat(amount) * 100)
      } else {
        $().uxmessage('error', 'Spendenbetrag muss größer 0 sein.')
        return
      }
    } else {
      $().uxmessage('error', 'Spendenbetrag muss Zahl sein.')
      return
    }
  } else if (chosen_material.name == "Bereits abgerechnet") {
    data.Betrag = 0
    data.Wiederverwendet = true
  } else {
    if (chose_material.size == chosen_quantity) {
      data.Betrag = -chosen_material.price_full 
    } else {
      data.Betrag = -chosen_material.price_part * chosen_quantity
    }
    data.Menge = chosen_quantity
    data.Material = chosen_material.name
    if (current_user == undefined) {
      $().uxmessage('notice', 'Kein Nutzer angemeldet')
    }
  }
  console.log(data)
  console.log(JSON.stringify(data))
  $.post(server_address + '/decrease_credit/21042017freilab1337fooboardasgeht111', JSON.stringify(data), function(result){
    if (result == 'true') {
      if (chosen_material.name == "Restekiste") {
        $('#restekiste_modal').modal('toggle')
      } else if (chosen_material.name == "Abgesprochenes Material") {
        $('#abgesprochen_modal').modal('toggle')
      } else if (chosen_material.name == "Bereits abgerechnet") {
        $('#wiederverwendet_modal').modal('toggle')
      } else if (chosen_max_quantity == 3) {
        $('#material_quantity_3_modal').modal('toggle')
      } else {
        $('#material_quantity_6_modal').modal('toggle')
      }
      run_btn_start()
    } else if (result == 'false') {
      $().uxmessage('error', 'Fehler')
    } else {
      $().uxmessage('error', result)
    }   
  }, 'json');
}