const textoProcesso = document.getElementById("textoProcesso");
const resultado = document.getElementById("resultado");
const actionButtons = document.querySelectorAll("button[data-action]");
const corujaBtn = document.getElementById("corujaBtn");
const corujaMenu = document.getElementById("corujaMenu");

function validarTexto() {
  const texto = textoProcesso.value.trim();
  if (!texto) {
    resultado.textContent = "Cole um texto para a Coruj IA iniciar a análise.";
    return null;
  }
  return texto;
}

function quebrarSentencas(texto) {
  return texto
    .split(/(?<=[.!?])\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function acaoResumo(texto) {
  const sentencas = quebrarSentencas(texto);
  const resumo = sentencas.slice(0, 5).join(" ");
  return `Resumo inicial (prévia):\n\n${resumo || "Não foi possível identificar frases completas."}`;
}

function acaoUltimaPeca(texto) {
  const linhas = texto
    .split("\n")
    .map((linha) => linha.trim())
    .filter(Boolean);
  const ultima = linhas.at(-1) || "Não identificada.";

  return `Última peça identificada (heurística):\n\n${ultima}\n\nSugestão: confirme pelo andamento oficial no sistema processual.`;
}

function acaoModelo(texto) {
  const temDenuncia = /denúncia|acusação|réu|crime/i.test(texto);
  const tipo = temDenuncia ? "alegações finais do Ministério Público" : "manifestação ministerial genérica";

  return `Sugestão de modelo (${tipo}):\n\n1. Relatório objetivo dos fatos e do andamento.\n2. Fundamentação jurídica com base legal e precedentes.\n3. Análise das provas e pontos controvertidos.\n4. Pedido final claro, certo e delimitado.\n\nPosso adaptar o modelo conforme o ramo (cível, criminal, infância, etc.).`;
}

function acaoRevisao(texto) {
  const problemas = [];
  if (texto.length < 300) problemas.push("Texto possivelmente curto para uma análise jurídica robusta.");
  if (/\bcoisa\b|\btal\b|\baí\b/i.test(texto)) problemas.push("Há termos coloquiais que podem ser substituídos por linguagem técnica.");
  if (!/[.;:]/.test(texto)) problemas.push("Pouca pontuação técnica identificada; revise períodos longos.");

  const cabecalho = "Revisão jurídica inicial:";
  const corpo = problemas.length ? problemas.map((item) => `- ${item}`).join("\n") : "- Estrutura e formalidade geral adequadas para uma primeira versão.";
  return `${cabecalho}\n\n${corpo}`;
}

function acaoContradicoes(texto) {
  const contradicoes = [];
  if (/não houve dano/i.test(texto) && /dano moral|dano material/i.test(texto)) {
    contradicoes.push("Consta afirmação de inexistência de dano e pedido/referência a dano no mesmo texto.");
  }
  if (/réu confessa/i.test(texto) && /nega autoria/i.test(texto)) {
    contradicoes.push("Há menção simultânea à confissão e à negativa de autoria.");
  }

  if (!contradicoes.length) {
    return "Não encontrei contradições textuais evidentes com as regras iniciais. Recomenda-se revisão humana detalhada.";
  }

  return `Possíveis contradições encontradas:\n\n${contradicoes.map((c) => `- ${c}`).join("\n")}`;
}

function executarAcao(action) {
  const texto = validarTexto();
  if (!texto) return;

  const mapa = {
    resumo: acaoResumo,
    "ultima-peca": acaoUltimaPeca,
    modelo: acaoModelo,
    revisao: acaoRevisao,
    contradicoes: acaoContradicoes,
  };

  const handler = mapa[action];
  if (!handler) return;

  resultado.textContent = "Analisando...";
  window.setTimeout(() => {
    resultado.textContent = handler(texto);
  }, 200);
}

actionButtons.forEach((btn) => {
  btn.addEventListener("click", () => executarAcao(btn.dataset.action));
});

corujaBtn.addEventListener("click", () => {
  const aberto = !corujaMenu.hidden;
  corujaMenu.hidden = aberto;
});

document.addEventListener("click", (event) => {
  const cliqueDentroMenu = corujaMenu.contains(event.target);
  const cliqueNoBotao = corujaBtn.contains(event.target);
  if (!cliqueDentroMenu && !cliqueNoBotao) {
    corujaMenu.hidden = true;
  }
});
