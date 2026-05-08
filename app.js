/* ─── CORUJ IA — app.js ────────────────────────────────────────
   GitHub Pages · chama API Anthropic diretamente do browser
   API key salva em localStorage("coruj_api_key")
────────────────────────────────────────────────────────────── */

// ─── MATÉRIAS ────────────────────────────────────────────────
const MATERIAS = [
  { cod: "TODAS", label: "Todas",            cor: "#64748b" },
  { cod: "FAM",   label: "Família",           cor: "#e53e3e" },
  { cod: "EXE",   label: "Execução",          cor: "#dd6b20" },
  { cod: "CRI",   label: "Criminal",          cor: "#805ad5" },
  { cod: "INF",   label: "Infância",          cor: "#38a169" },
  { cod: "CIV",   label: "Cível",             cor: "#3182ce" },
  { cod: "TUT",   label: "Tutela Coletiva",   cor: "#00b5d8" },
  { cod: "JUR",   label: "Tribunal do Júri",  cor: "#d53f8c" },
  { cod: "HAB",   label: "Habeas Corpus",     cor: "#b7791f" },
  { cod: "CAU",   label: "Cautelares",        cor: "#2f855a" },
  { cod: "CON",   label: "Controle Externo",  cor: "#553c9a" },
];

let materiaAtiva = "TODAS";

// ─── PROMPTS POR AÇÃO ─────────────────────────────────────────
const PROMPTS = {
  resumo: (texto, materia) => `
Você é um assistente jurídico especializado do Ministério Público de Mato Grosso do Sul.
Área de atuação: ${materia !== "TODAS" ? materia : "não especificada"}.

Elabore um RESUMO OBJETIVO E ESTRUTURADO do texto processual abaixo.
O resumo deve conter:
1. Identificação: tipo de peça, número do processo (se houver), partes, juízo.
2. Síntese dos fatos: cronologia dos eventos relevantes em 3–6 tópicos.
3. Estado atual do processo: qual a última movimentação e o que está pendente.
4. Ponto central para manifestação do MP (se houver).

Use linguagem técnica-jurídica, concisa, em prosa estruturada com subtítulos em negrito.

TEXTO:
${texto}
`.trim(),

  "ultima-peca": (texto, materia) => `
Você é um assistente jurídico especializado do Ministério Público.
Área: ${materia !== "TODAS" ? materia : "não especificada"}.

Analise o texto abaixo e identifique:
1. **Última peça/movimentação processual**: qual foi, data (se houver), quem praticou.
2. **Natureza do ato**: despacho, decisão interlocutória, sentença, petição, manifestação, etc.
3. **Prazo ou providência pendente**: há algum prazo correndo para o MP? Qual ato deve ser praticado em seguida?
4. **Prioridade sugerida**: urgente / normal / sem urgência.

Seja preciso e objetivo. Se não for possível identificar a última peça com certeza, indique a incerteza.

TEXTO:
${texto}
`.trim(),

  modelo: (texto, materia) => `
Você é um assistente jurídico do Ministério Público de Mato Grosso do Sul, especialista em elaboração de manifestações formais.
Área: ${materia !== "TODAS" ? materia : "determinar pela análise do texto"}.

Com base no texto processual abaixo, elabore uma MINUTA COMPLETA DE MANIFESTAÇÃO DO MP contendo:

**CABEÇALHO**
Excelentíssimo(a) Senhor(a) Doutor(a) [Juiz(a)],
Processo nº [extrair do texto ou deixar em branco]

**RELATÓRIO**
Breve relato do que os autos revelam até o momento.
"É o relatório."

**MÉRITO / ANÁLISE**
Fundamentos jurídicos pertinentes (leis, artigos, doutrina e jurisprudência do TJMS se aplicável).
Pontos favoráveis e contrários à tese do MP.

**REQUERIMENTO**
Pedido claro, certo e delimitado.

**ENCERRAMENTO**
"Termos em que pede deferimento."
Local e data.
[Promotor(a) de Justiça]

Use linguagem formal e técnica do MPMS. Adapte o modelo exatamente ao tipo de caso identificado.

TEXTO DO PROCESSO:
${texto}
`.trim(),

  revisao: (texto, materia) => `
Você é um revisor jurídico especializado do Ministério Público.
Área: ${materia !== "TODAS" ? materia : "não especificada"}.

Revise o texto jurídico abaixo e apresente:
1. **Problemas de clareza**: frases ambíguas, períodos excessivamente longos, falta de objetividade.
2. **Problemas de formalidade**: termos coloquiais, construções inadequadas para manifestações do MP.
3. **Problemas técnico-jurídicos**: uso impreciso de termos, classificações incorretas, fundamentação fraca.
4. **Problemas estruturais**: ausência de relatório, mérito ou pedido; falta de encerramento correto.
5. **Sugestões de melhoria**: reescreva os trechos problemáticos com a versão corrigida.
6. **Avaliação geral**: nota de 1 a 10 e parecer sintético.

Seja objetivo e construtivo.

TEXTO:
${texto}
`.trim(),

  contradicoes: (texto, materia) => `
Você é um analista jurídico do Ministério Público especializado em identificar inconsistências em peças processuais.
Área: ${materia !== "TODAS" ? materia : "não especificada"}.

Analise o texto abaixo e identifique:
1. **Contradições internas**: afirmações que se contradizem dentro do próprio texto.
2. **Contradições com regras jurídicas**: afirmações contrárias à lei, à jurisprudência consolidada ou à lógica processual.
3. **Inconsistências fáticas**: datas, nomes, valores ou circunstâncias incompatíveis entre si.
4. **Lacunas argumentativas**: pontos que precisam de fundamentação e estão sem suporte.
5. **Recomendações**: como resolver cada inconsistência encontrada.

Se não houver contradições evidentes, diga explicitamente e aponte os pontos que merecem atenção preventiva.

TEXTO:
${texto}
`.trim(),

  fundamentos: (texto, materia) => `
Você é um assistente jurídico do Ministério Público especializado em pesquisa legal.
Área: ${materia !== "TODAS" ? materia : "determinar pelo contexto"}.

Com base no texto abaixo, identifique e explique os FUNDAMENTOS JURÍDICOS relevantes:
1. **Dispositivos legais aplicáveis**: artigos da Constituição Federal, Código Penal, Código Civil, ECA, EStatuto de Defesa do Consumidor, etc.
2. **Princípios jurídicos**: quais princípios embasam a matéria (devido processo legal, contraditório, etc.).
3. **Jurisprudência relevante**: cite súmulas do STF/STJ ou tendência jurisprudencial do TJMS, se aplicável.
4. **Doutrina pertinente**: autores e obras relevantes para fundamentação da manifestação do MP.
5. **Estratégia de argumentação sugerida**: qual linha argumentativa é mais forte para o MP neste caso.

Seja específico e cite os dispositivos exatos.

TEXTO:
${texto}
`.trim(),
};

// ─── ESTADO ──────────────────────────────────────────────────
let emExecucao = false;

// ─── INIT ─────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  renderMaterias();
  bindEvents();
  verificarApiKey();
});

// ─── RENDER MATÉRIAS ──────────────────────────────────────────
function renderMaterias() {
  const lista = document.getElementById("listaMaterias");
  lista.innerHTML = "";
  MATERIAS.forEach(m => {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.className = "materia-pill" + (m.cod === materiaAtiva ? " active" : "");
    btn.innerHTML = `<span class="materia-dot" style="background:${m.cor}"></span>${m.label}`;
    btn.addEventListener("click", () => {
      materiaAtiva = m.cod;
      renderMaterias();
    });
    li.appendChild(btn);
    lista.appendChild(li);
  });
}

// ─── BIND EVENTS ──────────────────────────────────────────────
function bindEvents() {
  // Chips de ação
  document.querySelectorAll("[data-action]").forEach(btn => {
    btn.addEventListener("click", () => executar(btn.dataset.action));
  });

  // Contador de caracteres
  const textarea = document.getElementById("textoProcesso");
  const counter  = document.getElementById("charCount");
  textarea.addEventListener("input", () => {
    const n = textarea.value.length;
    counter.textContent = n.toLocaleString("pt-BR") + " caracteres";
  });

  // Fechar menu ao clicar fora
  document.addEventListener("click", e => {
    const menu = document.getElementById("corujaMenu");
    const fab  = document.getElementById("corujaBtn");
    if (!menu.hidden && !menu.contains(e.target) && !fab.contains(e.target)) {
      menu.hidden = true;
    }
  });
}

// ─── EXECUTAR ANÁLISE ─────────────────────────────────────────
async function executar(action) {
  if (emExecucao) return;

  const texto = document.getElementById("textoProcesso").value.trim();
  if (!texto) {
    mostrarToast("Cole um texto antes de analisar.");
    return;
  }

  const apiKey = localStorage.getItem("coruj_api_key") || "";
  if (!apiKey) {
    abrirConfig();
    mostrarToast("Configure sua chave da API Anthropic primeiro.");
    return;
  }

  const modelo = localStorage.getItem("coruj_modelo") || "claude-sonnet-4-5";
  const prompt = PROMPTS[action];
  if (!prompt) return;

  // UI: loading
  emExecucao = true;
  fecharMenu();
  setResultado("⏳ Analisando com a Coruj IA...");
  document.getElementById("loadingBar").hidden = false;

  try {
    const resp = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type":                         "application/json",
        "x-api-key":                            apiKey,
        "anthropic-version":                    "2023-06-01",
        "anthropic-dangerous-direct-browser-access": "true",
      },
      body: JSON.stringify({
        model:      modelo,
        max_tokens: 2048,
        messages: [
          { role: "user", content: prompt(texto, materiaAtiva) }
        ],
      }),
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      const msg = err?.error?.message || `Erro HTTP ${resp.status}`;
      if (resp.status === 401) {
        setResultado("❌ Chave de API inválida. Abra ⚙️ Configurações e verifique a chave.");
      } else {
        setResultado(`❌ Erro na API Anthropic:\n${msg}`);
      }
      return;
    }

    const data   = await resp.json();
    const answer = data?.content?.[0]?.text || "(sem resposta)";
    setResultado(answer);

  } catch (err) {
    setResultado(`❌ Falha de conexão:\n${err.message}\n\nVerifique sua conexão com a internet.`);
  } finally {
    emExecucao = false;
    document.getElementById("loadingBar").hidden = true;
  }
}

// ─── HELPERS DE UI ────────────────────────────────────────────
function setResultado(texto) {
  const el = document.getElementById("resultado");
  el.innerHTML = "";          // limpa placeholder
  el.textContent = texto;
}

function fecharMenu() {
  document.getElementById("corujaMenu").hidden = true;
}

function toggleMenu() {
  const menu = document.getElementById("corujaMenu");
  menu.hidden = !menu.hidden;
}

function mostrarToast(msg) {
  let t = document.querySelector(".toast");
  if (!t) {
    t = document.createElement("div");
    t.className = "toast";
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 3000);
}

// ─── COPIAR / BAIXAR ──────────────────────────────────────────
function copiarResultado() {
  const texto = document.getElementById("resultado").textContent;
  if (!texto || texto.includes("aparecerá aqui") || texto.includes("Selecione")) {
    mostrarToast("Nenhum resultado para copiar.");
    return;
  }
  navigator.clipboard.writeText(texto)
    .then(() => mostrarToast("✅ Copiado para a área de transferência!"))
    .catch(() => mostrarToast("Erro ao copiar. Selecione o texto manualmente."));
}

function baixarResultado() {
  const texto = document.getElementById("resultado").textContent;
  if (!texto || texto.includes("aparecerá aqui") || texto.includes("Selecione")) {
    mostrarToast("Nenhum resultado para baixar.");
    return;
  }
  const blob = new Blob([texto], { type: "text/plain;charset=utf-8" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = `coruj-ia-${Date.now()}.txt`;
  a.click();
  URL.revokeObjectURL(url);
  mostrarToast("📄 Download iniciado.");
}

// ─── CONFIG MODAL ─────────────────────────────────────────────
function abrirConfig() {
  const modal = document.getElementById("modalConfig");
  // Preenche campos com valores salvos
  document.getElementById("inputApiKey").value  = localStorage.getItem("coruj_api_key") || "";
  document.getElementById("selectModelo").value = localStorage.getItem("coruj_modelo")  || "claude-sonnet-4-5";
  modal.hidden = false;
}

function fecharConfig() {
  document.getElementById("modalConfig").hidden = true;
}

function salvarConfig() {
  const apiKey = document.getElementById("inputApiKey").value.trim();
  const modelo = document.getElementById("selectModelo").value;
  if (!apiKey) {
    mostrarToast("Informe a chave da API antes de salvar.");
    return;
  }
  localStorage.setItem("coruj_api_key", apiKey);
  localStorage.setItem("coruj_modelo",  modelo);
  fecharConfig();
  mostrarToast("✅ Configurações salvas!");
}

function verificarApiKey() {
  const apiKey = localStorage.getItem("coruj_api_key");
  if (!apiKey) {
    // Abre config automaticamente na primeira visita
    setTimeout(abrirConfig, 800);
  }
}

// Fechar modal clicando no overlay
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("modalConfig").addEventListener("click", e => {
    if (e.target === e.currentTarget) fecharConfig();
  });
});
