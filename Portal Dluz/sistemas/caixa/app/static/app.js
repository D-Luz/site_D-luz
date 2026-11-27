// Comportamentos da tela (CSP: sem script inline).
(function () {
  // Confirmação antes de enviar formulários marcados.
  document.querySelectorAll("form[data-confirmar]").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      if (!window.confirm(f.getAttribute("data-confirmar"))) e.preventDefault();
    });
  });

  // Troca de empresa/dia e saldo inicial: envia ao mudar.
  document.querySelectorAll("[data-enviar]").forEach(function (campo) {
    campo.addEventListener("change", function () { campo.form.submit(); });
  });
  document.querySelectorAll("[data-enviar-ao-mudar]").forEach(function (campo) {
    campo.addEventListener("change", function () { campo.form.submit(); });
  });

  // Campos de valor: ao entrar, seleciona o que já está escrito (digitar substitui).
  document.querySelectorAll("input.num").forEach(function (campo) {
    campo.addEventListener("focus", function () { campo.select(); });
  });

  var imprimir = document.querySelector("[data-imprimir]");
  if (imprimir) imprimir.addEventListener("click", function () { window.print(); });

  // Valor em reais digitado -> centavos (mesma regra do servidor).
  function centavos(texto) {
    var t = (texto || "").replace(/R\$|\s/g, "");
    if (!t) return null;
    if (t.indexOf(",") >= 0) t = t.replace(/\./g, "").replace(",", ".");
    else if (/^\d{1,3}(\.\d{3})+$/.test(t)) t = t.replace(/\./g, "");
    if (!/^\d+(\.\d{1,2})?$/.test(t)) return null;
    return Math.round(parseFloat(t) * 100);
  }
  function brl(c) {
    var s = c < 0 ? "−" : "";
    c = Math.abs(c);
    var r = Math.floor(c / 100).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    return s + r + "," + ("0" + (c % 100)).slice(-2);
  }

  // Linha nova: foco no valor e dica do PDV que tem esse valor (só sugestão).
  var valor = document.getElementById("novo-valor");
  var forma = document.getElementById("novo-forma");
  var dica = document.getElementById("novo-dica");
  var livres = document.getElementById("pdvs-livres");
  if (valor && !document.querySelector("tr.editando")) {
    valor.focus();
    valor.select();
    // Sem âncora na URL: a âncora mudaria o ponto de partida do Tab/Shift+Tab.
    valor.scrollIntoView({ block: "center" });
  }
  // Na forma, o número escolhe direto (1 dinheiro, 2 cartão...) e já volta para o valor.
  if (forma && valor) {
    forma.addEventListener("keydown", function (e) {
      if (!/^[1-9]$/.test(e.key) || e.ctrlKey || e.altKey || e.metaKey) return;
      var opcao = forma.options[parseInt(e.key, 10) - 1];
      if (!opcao) return;
      e.preventDefault();
      forma.value = opcao.value;
      forma.dispatchEvent(new Event("change"));
      valor.focus();
    });
  }
  if (valor && forma && dica && livres) {
    var pdvs = [];
    try { pdvs = JSON.parse(livres.textContent); } catch (e) { pdvs = []; }
    var atualizar = function () {
      var c = centavos(valor.value);
      dica.textContent = "";
      if (c === null) return;
      var achado = pdvs.filter(function (p) { return p.forma === forma.value && p.valor === c; })[0];
      var span = document.createElement("span");
      if (achado) {
        span.className = "dica-pdv";
        span.textContent = "PDV " + achado.pdv + " · " + achado.hora + " · " + achado.vendedor;
      } else if (["dinheiro", "cartao", "deposito", "cheque"].indexOf(forma.value) >= 0) {
        span.className = "dica-nada";
        span.textContent = "nenhum PDV livre com esse valor (ainda)";
      }
      dica.appendChild(span);
    };
    valor.addEventListener("input", atualizar);
    forma.addEventListener("change", atualizar);
    atualizar();
  }

  // Encerramento: diferença da gaveta enquanto digita o contado.
  var contado = document.getElementById("contado");
  var esperado = document.getElementById("esperado");
  var dif = document.getElementById("dif-gaveta");
  if (contado && esperado && dif) {
    var calc = function () {
      var c = centavos(contado.value);
      if (contado.value.trim() === "0") c = 0;
      if (c === null) { dif.textContent = ""; return; }
      var d = c - parseInt(esperado.getAttribute("data-centavos"), 10);
      dif.textContent = d === 0 ? "Gaveta bate." : "Diferença na gaveta: " + (d > 0 ? "+" : "") + brl(d);
      dif.className = d === 0 ? "ok-texto" : "negativo";
    };
    contado.addEventListener("input", calc);
    calc();
  }
})();
