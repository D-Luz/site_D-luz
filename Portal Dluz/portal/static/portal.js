// Portal Dluz — comportamento das telas (o CSP não permite script inline).
(function () {
  "use strict";

  function el(tag, attrs, texto) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (texto !== undefined) e.textContent = texto;
    return e;
  }

  // Menu no celular
  var botaoMenu = document.getElementById("botao-menu");
  if (botaoMenu) {
    botaoMenu.addEventListener("click", function () {
      var aberto = document.getElementById("topo").classList.toggle("aberto");
      botaoMenu.setAttribute("aria-expanded", aberto ? "true" : "false");
    });
  }

  // Menu da conta: fecha ao clicar fora
  document.addEventListener("click", function (ev) {
    document.querySelectorAll("details.conta[open]").forEach(function (d) {
      if (!d.contains(ev.target)) d.removeAttribute("open");
    });
  });

  // Formulário de ferramenta: evita clique duplo
  var form = document.getElementById("form-ferramenta");
  if (form) {
    form.addEventListener("submit", function () {
      var b = document.getElementById("botao-gerar");
      if (b) { b.disabled = true; b.textContent = "Enviando…"; }
    });
  }

  // Tarefa em andamento: acompanha o registro e recarrega ao terminar
  var tarefa = document.getElementById("tarefa");
  if (tarefa && (tarefa.dataset.situacao === "fila" || tarefa.dataset.situacao === "rodando")) {
    var registro = document.getElementById("registro");
    var consultar = function () {
      fetch(tarefa.dataset.estado, { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.log) {
            var noFim = registro.scrollTop + registro.clientHeight >= registro.scrollHeight - 20;
            registro.textContent = d.log;
            if (noFim) registro.scrollTop = registro.scrollHeight;
          }
          if (d.situacao === "ok" || d.situacao === "erro") { location.reload(); return; }
          setTimeout(consultar, 1500);
        })
        .catch(function () { setTimeout(consultar, 4000); });
    };
    setTimeout(consultar, 800);
  }

  // Configurações: testar conexão com os servidores do ATS
  var testar = document.getElementById("testar-conexao");
  if (testar) {
    testar.addEventListener("click", function () {
      var saida = document.getElementById("resultado-teste");
      var csrf = document.querySelector("input[name=csrf]").value;
      testar.disabled = true;
      saida.textContent = "Testando…";
      fetch(testar.dataset.url, { method: "POST", credentials: "same-origin", headers: { "X-CSRF": csrf } })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          saida.textContent = Object.keys(d).map(function (k) {
            return k + ": " + (d[k] === "ok" ? "respondendo" : "sem conexão (" + d[k] + ")");
          }).join(" · ");
        })
        .catch(function () { saida.textContent = "Falha ao testar."; })
        .then(function () { testar.disabled = false; });
    });
  }

  // Editor de itens da proposta de orçamento
  var editor = document.getElementById("itens-editor");
  if (!editor) return;
  var campoJson = document.getElementById("itens-json");
  var corpo = editor.querySelector("tbody");
  var COLUNAS = [
    ["ambiente", 10], ["p_cod", 5], ["p_desc", 22], ["p_un", 3], ["p_qtd", 4], ["p_vunit", 7],
    ["s_cod", 5], ["s_desc", 18], ["s_un", 3], ["s_qtd", 4], ["s_vunit", 7]
  ];
  var moeda = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

  function numero(texto) {
    var t = String(texto || "").trim().replace(/[R$\s]/g, "");
    if (t.indexOf(",") >= 0) t = t.replace(/\./g, "").replace(",", ".");
    var n = parseFloat(t);
    return isNaN(n) ? 0 : n;
  }
  function textoNumero(v) {
    if (v === undefined || v === null || v === "" || Number(v) === 0) return "";
    return String(v).replace(".", ",");
  }

  function linha(item) {
    item = item || {};
    var tr = el("tr");
    COLUNAS.forEach(function (c) {
      var td = el("td");
      var valor = /qtd|vunit/.test(c[0]) ? textoNumero(item[c[0]]) : (item[c[0]] || "");
      var inp = el("input", { type: "text", "data-c": c[0], size: String(c[1]), value: valor });
      if (/qtd|vunit/.test(c[0])) inp.setAttribute("inputmode", "decimal");
      td.appendChild(inp);
      tr.appendChild(td);
    });
    var tot = el("td", { "class": "total" });
    tr.appendChild(tot);
    var tdx = el("td");
    var x = el("button", { type: "button", "class": "secundario pequeno", title: "Remover" }, "×");
    x.addEventListener("click", function () { tr.remove(); atualizar(); });
    tdx.appendChild(x);
    tr.appendChild(tdx);
    if (item.id) tr.dataset.id = item.id;
    corpo.appendChild(tr);
    return tr;
  }

  function atualizar() {
    var itens = [], total = 0;
    corpo.querySelectorAll("tr").forEach(function (tr) {
      var it = {};
      tr.querySelectorAll("input[data-c]").forEach(function (i) { it[i.dataset.c] = i.value.trim(); });
      ["p_qtd", "p_vunit", "s_qtd", "s_vunit"].forEach(function (k) { it[k] = numero(it[k]); });
      it.p_vtot = it.p_qtd * it.p_vunit;
      it.s_vtot = it.s_qtd * it.s_vunit;
      if (tr.dataset.id) it.id = tr.dataset.id;
      var t = it.p_vtot + it.s_vtot;
      tr.querySelector(".total").textContent = t ? moeda.format(t) : "";
      total += t;
      if (it.ambiente || it.p_desc || it.s_desc) itens.push(it);
    });
    campoJson.value = JSON.stringify(itens);
    document.getElementById("itens-total").textContent = moeda.format(total);
  }

  function carregar(itens) {
    corpo.replaceChildren();
    (itens || []).forEach(function (i) { linha(i); });
    if (!corpo.children.length) linha({});
    atualizar();
  }

  editor.addEventListener("input", atualizar);
  document.getElementById("item-novo").addEventListener("click", function () {
    var ult = corpo.lastElementChild;
    var amb = ult ? ult.querySelector("input[data-c=ambiente]").value : "";
    linha({ ambiente: amb }).querySelector("input[data-c=p_cod]").focus();
    atualizar();
  });

  function preencher(nome, valor) {
    var c = form.querySelector("[name=" + nome + "]");
    if (!c) return;
    if (c.type === "checkbox") c.checked = !!valor; else c.value = valor === undefined || valor === null ? "" : valor;
  }

  // Projeto salvo (.json): mesmo formato do Painel de Orçamentos antigo
  function aplicarProjeto(p) {
    preencher("cliente", p.cliente); preencher("vendedor", p.vendedor);
    preencher("contato", p.contato); preencher("data", p.data);
    var g = p.pagamento || {};
    preencher("avista", g.incluir_avista); preencher("avista_modo", g.avista_modo || "desconto");
    preencher("desconto_avista", g.desconto_avista_texto); preencher("valor_avista", g.valor_avista_texto);
    preencher("parcelado_desconto", g.incluir_desconto_parcelas); preencher("desconto_parcelado", g.desconto_parcelado_texto);
    preencher("parcelas_desconto", g.parcelas_desconto); preencher("parcelado", g.incluir_parcelas_sem_desconto);
    preencher("parcelas", g.parcelas_sem_desconto);
    carregar(p.itens || []);
  }

  document.getElementById("carregar-projeto").addEventListener("change", function (ev) {
    var arq = ev.target.files[0];
    if (!arq) return;
    arq.text().then(function (t) {
      try { aplicarProjeto(JSON.parse(t)); } catch (e) { alert("Arquivo de projeto inválido."); }
    });
  });

  var inicial = [];
  try { inicial = JSON.parse(campoJson.value || "[]"); } catch (e) { inicial = []; }
  if (editor.dataset.projeto) {
    try { aplicarProjeto(JSON.parse(editor.dataset.projeto)); } catch (e) { carregar(inicial); }
  } else {
    carregar(inicial);
  }
})();
