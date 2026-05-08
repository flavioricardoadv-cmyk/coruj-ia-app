const MODELS = [
  {
    area: "Curatela e Capacidade Civil",
    title: "Curatela - ciencia de liminar e entrevista designada",
    body: "Excelentissimo(a) Senhor(a) Doutor(a) Juiz(a),\n\nO Ministerio Publico, nos autos em referencia, manifesta ciencia da decisao liminar e requer o regular prosseguimento do feito, com a realizacao da entrevista designada e posterior vista para analise das informacoes colhidas.\n\nTermos em que pede deferimento."
  },
  {
    area: "Curatela e Capacidade Civil",
    title: "Curatela - autora inerte/Defensoria e possivel extincao",
    body: "O Ministerio Publico verifica a inercia da parte autora e requer sua intimacao pessoal, bem como a comunicacao da Defensoria Publica, se cabivel, antes de eventual extincao do processo."
  },
  {
    area: "Curatela e Capacidade Civil",
    title: "Curatela - necessidade de pericia/estudo social/oitiva de familiares",
    body: "Considerando a natureza da demanda, o Ministerio Publico requer a complementacao da instrucao, com pericia tecnica, estudo social e oitiva de familiares proximos, a fim de subsidiar decisao segura."
  },
  {
    area: "Execucao de Alimentos",
    title: "Rito da prisao - executado intimado e inerte",
    body: "O Ministerio Publico, diante da intimacao regular do executado e de sua inercia, manifesta-se pelo prosseguimento do feito pelo rito da prisao civil, observadas as cautelas legais."
  },
  {
    area: "Execucao de Alimentos",
    title: "Acordo de parcelamento em execucao de alimentos",
    body: "O Ministerio Publico toma ciencia do acordo noticiado e requer a homologacao, com advertencia quanto ao vencimento antecipado em caso de inadimplemento."
  },
  {
    area: "Familia e Sucessoes",
    title: "Guarda e convivencia - estudo psicossocial",
    body: "O Ministerio Publico requer estudo psicossocial para apuracao da dinamica familiar, preservando-se o melhor interesse da crianca ou adolescente."
  },
  {
    area: "Mandado de Seguranca",
    title: "Parecer pela denegacao da seguranca",
    body: "Ausente direito liquido e certo demonstrado de plano, o Ministerio Publico opina pela denegacao da seguranca, sem prejuizo das vias ordinarias cabiveis."
  },
  {
    area: "Saude",
    title: "Obrigacao de fazer - medicamento/tratamento",
    body: "O Ministerio Publico requer a comprovacao da necessidade atual do tratamento, relatorio medico atualizado e manifestacao do ente publico responsavel."
  }
];

const LANDSCAPES = [
  { src: "public/assets/landscapes/landscape-lake-dawn.jpg", caption: "Amanhecer tranquilo" },
  { src: "public/assets/landscapes/landscape-forest-valley.jpg", caption: "Vale verde" },
  { src: "public/assets/landscapes/landscape-coast-sunset.jpg", caption: "Litoral ao entardecer" },
  { src: "public/assets/landscapes/landscape-highland-night.jpg", caption: "Serra silenciosa" }
];

const OWL_STATES = {
  idle: "public/assets/owl/owl-idle.png",
  writing: "public/assets/owl/owl-writing.png",
  investigate: "public/assets/owl/owl-investigate.png",
  deadline: "public/assets/owl/owl-deadline.png",
  warning: "public/assets/owl/owl-warning.png",
  checklist: "public/assets/owl/owl-checklist.png",
  success: "public/assets/owl/owl-success.png",
  teacher: "public/assets/owl/owl-teacher.png"
};

const OWL_ACTIONS = [
  ["summarize", "investigate", "RS", "Resumir processo", "Sintese objetiva dos autos"],
  ["urgency", "deadline", "UR", "Analisar urgencia", "Prazos, risco e prioridade"],
  ["pages", "checklist", "FL", "Conferir fls.", "Checklist de completude"],
  ["pending", "warning", "PD", "Identificar pendencias", "Lacunas e providencias"],
  ["report", "writing", "RL", "Gerar relatorio", "Relatorio preliminar"],
  ["opinion", "writing", "PR", "Gerar parecer", "Minuta de parecer"],
  ["rewrite", "writing", "RT", "Reescrever trecho", "Linguagem tecnica"],
  ["explain", "teacher", "EX", "Explicar decisao", "Traducao juridica"],
  ["next", "success", "NA", "Sugerir proximo ato", "Caminho processual"]
];

let selectedModel = null;
let landscapeIndex = 0;
let landscapeTimer = null;

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setStatus(message) {
  document.getElementById("statusText").textContent = message;
}

function renderModels(filter = "") {
  const box = document.getElementById("modelsTree");
  const normalized = filter.trim().toLowerCase();
  const visible = MODELS.filter(model =>
    !normalized ||
    model.area.toLowerCase().includes(normalized) ||
    model.title.toLowerCase().includes(normalized)
  );
  const grouped = visible.reduce((acc, model) => {
    acc[model.area] = acc[model.area] || [];
    acc[model.area].push(model);
    return acc;
  }, {});

  box.innerHTML = "";
  Object.keys(grouped).sort().forEach((area, index) => {
    const group = document.createElement("div");
    group.className = "area-group" + (index === 0 ? " expanded" : "");
    group.innerHTML = `<div class="area-title">${escapeHtml(area)}</div><div class="model-list"></div>`;
    group.querySelector(".area-title").addEventListener("click", () => group.classList.toggle("expanded"));
    const list = group.querySelector(".model-list");
    grouped[area].forEach(model => {
      const item = document.createElement("div");
      item.className = "model";
      item.textContent = model.title;
      item.addEventListener("click", () => selectModel(model, item));
      list.appendChild(item);
    });
    box.appendChild(group);
  });
}

function selectModel(model, item) {
  selectedModel = model;
  document.querySelectorAll(".model.active").forEach(el => el.classList.remove("active"));
  item.classList.add("active");
  document.getElementById("selectedTitle").textContent = model.title;
  document.getElementById("selectedMeta").textContent = model.area;
  document.getElementById("previewIdle").classList.add("hidden");
  const preview = document.getElementById("modelPreview");
  preview.textContent = model.body;
  preview.classList.remove("hidden");
  setStatus("1 objeto selecionado");
}

function applyLandscape() {
  const idle = document.getElementById("previewIdle");
  const item = LANDSCAPES[landscapeIndex % LANDSCAPES.length];
  idle.style.backgroundImage = `url("${item.src}")`;
  idle.dataset.caption = item.caption;
}

function startLandscapes() {
  applyLandscape();
  landscapeTimer = window.setInterval(() => {
    const idle = document.getElementById("previewIdle");
    if (idle.classList.contains("hidden")) return;
    idle.classList.add("is-fading");
    window.setTimeout(() => {
      landscapeIndex = (landscapeIndex + 1) % LANDSCAPES.length;
      applyLandscape();
      idle.classList.remove("is-fading");
    }, 350);
  }, 6500);
}

function recommendModel() {
  const text = document.getElementById("caseText").value.trim();
  const result = document.getElementById("recommendResult");
  if (!text) {
    result.innerHTML = '<div class="training-note">Cole o texto do caso para recomendar um modelo.</div>';
    return;
  }

  const lower = text.toLowerCase();
  const scored = MODELS.map(model => {
    let score = 35;
    const haystack = `${model.area} ${model.title}`.toLowerCase();
    for (const word of lower.split(/\W+/).filter(w => w.length > 4)) {
      if (haystack.includes(word)) score += 8;
    }
    if (lower.includes("curatela") && model.area.includes("Curatela")) score += 35;
    if (lower.includes("alimento") && model.area.includes("Execucao")) score += 35;
    if (lower.includes("seguranca") && model.area.includes("Mandado")) score += 35;
    if (lower.includes("medicamento") && model.area.includes("Saude")) score += 35;
    return { model, score: Math.min(score, 98) };
  }).sort((a, b) => b.score - a.score).slice(0, 3);

  result.innerHTML = scored.map(({ model, score }) => `
    <div class="result-card">
      <b>${escapeHtml(model.title)}</b>
      <div class="meta">${escapeHtml(model.area)} | aderencia estimada: ${score}%</div>
      <div>${escapeHtml(model.body.slice(0, 180))}...</div>
    </div>
  `).join("");
  setOwlState("investigate");
}

function newCase() {
  document.getElementById("caseText").value = "";
  document.getElementById("recommendResult").innerHTML = "";
  setStatus("0 objetos selecionados");
  setOwlState("idle");
}

function setOwlState(state) {
  const src = OWL_STATES[state] || OWL_STATES.idle;
  document.getElementById("owlImage").src = src;
  document.getElementById("owlPanelImage").src = src;
}

function renderOwlActions() {
  const box = document.getElementById("owlActions");
  box.innerHTML = OWL_ACTIONS.map(([action, state, mark, label, hint]) => `
    <button class="owl-action" type="button" data-action="${action}" data-state="${state}">
      <span class="owl-action-mark">${mark}</span>
      <span><span>${escapeHtml(label)}</span><small>${escapeHtml(hint)}</small></span>
    </button>
  `).join("");
}

function toggleOwlMenu() {
  const menu = document.getElementById("owlMenu");
  const root = document.getElementById("owlAssistant");
  const open = menu.hidden;
  menu.hidden = !open;
  root.classList.toggle("is-menu-open", open);
}

function runOwlAction(action, state) {
  setOwlState(state);
  document.getElementById("owlMenu").hidden = true;
  document.getElementById("owlAssistant").classList.remove("is-menu-open");
  const text = document.getElementById("caseText").value.trim();
  const context = selectedModel ? selectedModel.title : "sem modelo selecionado";
  const titles = {
    summarize: "Resumo do processo",
    urgency: "Analise de urgencia",
    pages: "Conferencia de fls.",
    pending: "Pendencias identificadas",
    report: "Relatorio preliminar",
    opinion: "Parecer preliminar",
    rewrite: "Trecho reescrito",
    explain: "Explicacao juridica",
    next: "Proximo ato sugerido"
  };
  document.getElementById("owlPanelTitle").textContent = titles[action] || "Codex Coruj IA";
  document.getElementById("owlPanelBody").innerHTML = `
    <div class="owl-section">
      <div class="owl-section-label">Contexto</div>
      <div>${escapeHtml(context)}</div>
    </div>
    <div class="owl-section">
      <div class="owl-section-label">Leitura inicial</div>
      <div>${escapeHtml(text ? text.slice(0, 420) : "Nenhum texto foi informado. Cole o andamento ou selecione um modelo para melhorar a resposta.")}</div>
    </div>
    <div class="owl-section">
      <div class="owl-section-label">Sugestao</div>
      <ul class="owl-list">
        <li>Confirmar a ultima movimentacao nos autos.</li>
        <li>Verificar prazo, intimacoes pendentes e documentos essenciais.</li>
        <li>Usar o modelo sugerido apenas como minuta inicial, com revisao humana.</li>
      </ul>
    </div>
  `;
  document.getElementById("owlPanel").hidden = false;
}

function bindEvents() {
  document.getElementById("modelSearch").addEventListener("input", event => renderModels(event.target.value));
  document.getElementById("recommendBtn").addEventListener("click", recommendModel);
  document.getElementById("newCaseBtn").addEventListener("click", newCase);
  document.getElementById("ratingsBtn").addEventListener("click", () => {
    document.getElementById("recommendResult").innerHTML = '<div class="training-note">Avaliacoes ficam disponiveis no app local com backend.</div>';
  });
  document.getElementById("trainBtn").addEventListener("click", () => {
    document.getElementById("recommendResult").innerHTML = '<div class="training-note">Treinamento em lote disponivel no app local FastAPI. O GitHub Pages roda em modo demonstrativo.</div>';
  });
  document.getElementById("quickSearch").addEventListener("keydown", event => {
    if (event.key === "Enter") {
      document.getElementById("caseText").focus();
      setStatus(`Busca rapida: ${event.target.value || "sem termo"}`);
    }
  });
  document.querySelectorAll(".saj-toolbar button").forEach(button => {
    button.addEventListener("click", () => setStatus(`${button.textContent}: recurso demonstrativo online.`));
  });
  document.getElementById("owlButton").addEventListener("click", toggleOwlMenu);
  document.getElementById("closePanel").addEventListener("click", () => {
    document.getElementById("owlPanel").hidden = true;
    setOwlState("idle");
  });
  document.getElementById("owlActions").addEventListener("click", event => {
    const button = event.target.closest(".owl-action");
    if (button) runOwlAction(button.dataset.action, button.dataset.state);
  });
  document.addEventListener("click", event => {
    if (!event.target.closest("#owlAssistant")) {
      document.getElementById("owlMenu").hidden = true;
      document.getElementById("owlAssistant").classList.remove("is-menu-open");
    }
  });
}

renderModels();
renderOwlActions();
startLandscapes();
bindEvents();
