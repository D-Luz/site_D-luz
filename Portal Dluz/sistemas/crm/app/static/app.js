// Busca de cliente do ERP no cadastro de eletricista (CSP: sem script inline).
(function () {
  var campo = document.getElementById("busca-cliente");
  if (!campo) return;
  var lista = document.getElementById("busca-res");
  var codigo = document.getElementById("cliente_codigo");
  var empresa = document.getElementById("empresa");
  var nome = document.getElementById("nome");
  var fone = document.getElementById("telefone");
  var timer;
  campo.addEventListener("input", function () {
    clearTimeout(timer);
    timer = setTimeout(function () {
      var q = campo.value.trim();
      if (q.length < 3) { lista.innerHTML = ""; return; }
      fetch((campo.dataset.url || "/clientes/buscar") + "?q=" + encodeURIComponent(q), { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (itens) {
          lista.innerHTML = "";
          itens.forEach(function (c) {
            var li = document.createElement("li");
            li.textContent = c.nome + " · " + c.empresa + " " + c.codigo;
            li.addEventListener("click", function () {
              codigo.value = c.codigo; empresa.value = c.empresa;
              if (campo.dataset.preencher === "1") {
                if (nome && !nome.value) nome.value = c.nome;
                if (fone && !fone.value && c.telefone) fone.value = c.telefone;
              }
              campo.value = c.nome + " (" + c.empresa + " " + c.codigo + ")";
              lista.innerHTML = "";
            });
            lista.appendChild(li);
          });
        });
    }, 250);
  });
})();
