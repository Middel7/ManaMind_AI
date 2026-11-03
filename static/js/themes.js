/* Collections a theme : l'editeur de criteres, partage par la liste des
   collections (creation) et la page d'une collection (modification). */
(() => {
  'use strict';

  const esc = MM.esc;

  const NUMERIC_OPS = [
    ['eq', 'égal à'], ['gte', 'au moins'], ['lte', 'au plus'],
    ['gt', 'plus de'], ['lt', 'moins de'],
  ];
  const COLOR_OPS = [['exact', 'exactement'], ['include', 'au moins'], ['within', 'au plus']];
  const COLORS = [['W', 'Blanc'], ['U', 'Bleu'], ['B', 'Noir'], ['R', 'Rouge'],
                  ['G', 'Vert'], ['C', 'Incolore']];
  const RARITIES = [['common', 'Commune'], ['uncommon', 'Peu commune'], ['rare', 'Rare'],
                    ['mythic', 'Mythique'], ['special', 'Spéciale'], ['bonus', 'Bonus']];
  const FRAMES = [['1993', 'Ancien (1993)'], ['1997', 'Ancien (1997)'],
                  ['2003', 'Moderne (2003)'], ['2015', 'Actuel (2015)'], ['future', 'Futuriste']];
  const BORDERS = [['black', 'Noire'], ['white', 'Blanche'], ['borderless', 'Sans bordure'],
                   ['gold', 'Dorée'], ['silver', 'Argentée'], ['yellow', 'Jaune']];

  // Chaque champ : son libelle, la forme de sa valeur, et une aide de saisie.
  const FIELDS = {
    type: { label: 'Type ou sous-type', kind: 'text', hint: 'Angel, Legendary, Equipment…' },
    name: { label: 'Nom de la carte', kind: 'text', hint: 'contient…' },
    text: { label: 'Texte de règles', kind: 'text', hint: 'contient…' },
    keyword: { label: 'Mot-clé', kind: 'text', hint: 'Flying, Lifelink…' },
    mana_value: { label: 'Coût en mana', kind: 'number' },
    colors: { label: 'Couleurs', kind: 'colors' },
    identity: { label: 'Identité de couleur', kind: 'colors' },
    power: { label: 'Force', kind: 'number' },
    toughness: { label: 'Endurance', kind: 'number' },
    rarity: { label: 'Rareté', kind: 'list', choices: RARITIES },
    set: { label: 'Extension', kind: 'sets', hint: 'codes séparés par des virgules : DMU, M10' },
    year: { label: 'Année de sortie', kind: 'number' },
    artist: { label: 'Artiste', kind: 'text', hint: 'contient…' },
    frame: { label: 'Cadre', kind: 'choice', choices: FRAMES },
    border: { label: 'Bordure', kind: 'choice', choices: BORDERS },
    full_art: { label: 'Illustration pleine carte', kind: 'flag' },
    promo: { label: 'Promo', kind: 'flag' },
    commander: { label: 'Autorisée en Commander', kind: 'flag' },
    function: { label: 'Fonction de la carte', kind: 'function', hint: 'draw engine, ramp…' },
    price: { label: 'Prix (€)', kind: 'number' },
  };

  const options = (pairs, current) => pairs.map(([value, label]) =>
    `<option value="${esc(value)}" ${String(current) === String(value) ? 'selected' : ''}>
       ${esc(label)}</option>`).join('');

  /** Une ligne de critere, a partir de sa valeur enregistree. */
  function rowHtml(criterion) {
    const field = FIELDS[criterion.field] ? criterion.field : 'type';
    const spec = FIELDS[field];
    const value = criterion.value;
    let input = '';
    if (spec.kind === 'text' || spec.kind === 'function') {
      input = `<input class="input" data-value value="${esc(value || '')}"
                 placeholder="${esc(spec.hint || '')}"
                 ${spec.kind === 'function' ? 'list="mmThemeFunctions"' : ''}>`;
    } else if (spec.kind === 'number') {
      input = `<select class="select" data-op>${options(NUMERIC_OPS, criterion.op || 'eq')}</select>
               <input class="input" data-value type="number" step="any"
                      value="${esc(value ?? '')}">`;
    } else if (spec.kind === 'colors') {
      const picked = new Set(value || []);
      input = `<select class="select" data-op>${options(COLOR_OPS, criterion.op || 'exact')}</select>
               <span class="chips">${COLORS.map(([code, label]) => `
                 <button type="button" class="chip chip--color" data-pick="${code}"
                         aria-pressed="${picked.has(code)}">${esc(label)}</button>`).join('')}</span>`;
    } else if (spec.kind === 'list') {
      const picked = new Set(value || []);
      input = `<span class="chips">${spec.choices.map(([code, label]) => `
                 <button type="button" class="chip" data-pick="${code}"
                         aria-pressed="${picked.has(code)}">${esc(label)}</button>`).join('')}</span>`;
    } else if (spec.kind === 'sets') {
      input = `<input class="input" data-value value="${esc((value || []).join(', ').toUpperCase())}"
                 placeholder="${esc(spec.hint)}">`;
    } else if (spec.kind === 'choice') {
      input = `<select class="select" data-value>${options(spec.choices, value)}</select>`;
    }
    return `
      <div class="row theme-crit" data-field="${field}">
        <select class="select theme-crit__not" data-not aria-label="Inclure ou exclure">
          ${options([['', 'Avec'], ['1', 'Sauf']], criterion.not ? '1' : '')}</select>
        <select class="select theme-crit__field" data-field-select aria-label="Critère">
          ${Object.entries(FIELDS).map(([key, f]) =>
            `<option value="${key}" ${key === field ? 'selected' : ''}>${esc(f.label)}</option>`).join('')}
        </select>
        <span class="theme-crit__value">${input}</span>
        <button type="button" class="btn btn--ghost btn--icon btn--sm" data-remove
                aria-label="Retirer ce critère">${MM.icons.close}</button>
      </div>`;
  }

  /** Relit une ligne de critere telle que l'utilisateur l'a reglee. */
  function readRow(node) {
    const field = node.dataset.field;
    const spec = FIELDS[field];
    const criterion = { field };
    if (MM.el('[data-not]', node).value) criterion.not = true;
    const op = MM.el('[data-op]', node);
    if (op) criterion.op = op.value;
    const input = MM.el('[data-value]', node);
    if (spec.kind === 'colors' || spec.kind === 'list') {
      criterion.value = MM.els('[data-pick][aria-pressed="true"]', node)
        .map((chip) => chip.dataset.pick);
    } else if (spec.kind === 'sets') {
      criterion.value = input.value.split(/[\s,;]+/).filter(Boolean);
    } else if (spec.kind === 'number') {
      criterion.value = input.value;
    } else if (input) {
      criterion.value = input.value.trim();
    }
    return criterion;
  }

  let functions = null;

  /**
   * Ouvre l'editeur. `initial` porte une collection a modifier, sinon une
   * nouvelle est creee. `onSaved(id)` suit l'enregistrement.
   */
  function editor({ initial = null, onSaved } = {}) {
    const start = initial || { name: '', language: 'en', include_alt: false,
                               criteria: [{ field: 'type', value: '' }] };
    const dialog = MM.modal({
      title: initial ? 'Modifier la collection' : 'Nouvelle collection à thème',
      wide: true,
      body: `
        <div class="stack theme-editor">
          <div class="grid-2">
            <label class="field"><span class="label">Nom</span>
              <input class="input" id="thName" value="${esc(start.name)}"
                     placeholder="Les anges, Tout ce qui coûte 4…"></label>
            <label class="field"><span class="label">Langue des éditions</span>
              <select class="select" id="thLang">${options(MM.languages, start.language)}</select></label>
          </div>
          <div class="field">
            <span class="label">Critères — une carte doit les remplir tous</span>
            <div class="stack-2" id="thCriteria">${start.criteria.map(rowHtml).join('')}</div>
            <div><button type="button" class="btn btn--sm" id="thAdd">
              ${MM.icons.plus} Ajouter un critère</button></div>
          </div>
          <label class="switch">
            <input type="checkbox" id="thAlt" ${start.include_alt ? 'checked' : ''}>
            <span class="switch__track"></span>
            <span class="small">Inclure les éditions numériques, Secret Lair, Marvel Universe,
              Tortues Ninja et Avatar Eternal</span>
          </label>
          <div class="panel"><div class="panel__body stack-2" id="thPreview">
            <p class="small muted">L'aperçu compte les cartes et les éditions trouvées.</p>
          </div></div>
          <datalist id="mmThemeFunctions"></datalist>
        </div>`,
      footer: `
        <button class="btn" type="button" id="thPreviewBtn">Aperçu</button>
        <button class="btn btn--primary" type="button" id="thSave">Enregistrer</button>`,
    });
    const root = dialog.root;
    const list = MM.el('#thCriteria', root);

    // Les fonctions de cartes portent des noms anglais : la liste en propose
    // les plus courantes plutot que de les laisser deviner.
    (async () => {
      try {
        functions = functions || (await MM.api.get('/api/v2/themes/functions')).functions;
        MM.el('#mmThemeFunctions', root).innerHTML = functions
          .map((name) => `<option value="${esc(name)}">`).join('');
      } catch {}
    })();

    const definition = () => ({
      name: MM.el('#thName', root).value.trim(),
      language: MM.el('#thLang', root).value,
      include_alt: MM.el('#thAlt', root).checked,
      criteria: MM.els('.theme-crit', list).map(readRow),
    });

    MM.el('#thAdd', root).addEventListener('click', () => {
      list.insertAdjacentHTML('beforeend', rowHtml({ field: 'type', value: '' }));
    });

    list.addEventListener('change', (event) => {
      if (!event.target.matches('[data-field-select]')) return;
      const row = event.target.closest('.theme-crit');
      const not = MM.el('[data-not]', row).value === '1';
      row.outerHTML = rowHtml({ field: event.target.value, not });
    });

    list.addEventListener('click', (event) => {
      const pick = event.target.closest('[data-pick]');
      if (pick) {
        pick.setAttribute('aria-pressed', String(pick.getAttribute('aria-pressed') !== 'true'));
        return;
      }
      const remove = event.target.closest('[data-remove]');
      if (remove) remove.closest('.theme-crit').remove();
    });

    async function preview() {
      const host = MM.el('#thPreview', root);
      host.innerHTML = '<p class="small muted">Calcul…</p>';
      try {
        const data = await MM.api.post('/api/v2/themes/preview', definition());
        const s = data.stats;
        host.innerHTML = `
          <p class="small"><span class="strong">${MM.fmt.plural(s.cards, 'carte')}</span>,
            ${MM.fmt.plural(s.editions, 'édition')} · vous en possédez
            <span class="strong">${MM.fmt.int(s.owned)}</span></p>
          ${data.sample.length ? `<div class="card-grid card-grid--sm">${data.sample.map((e) => `
            <article class="mtg-card mtg-card--flat" data-name="${esc(e.name)}">
              <div class="mtg-card__frame">${MM.img.frame({ card_name: e.name,
                image_normal: e.image_normal, image_small: e.image_small }, { size: 'small' })}</div>
            </article>`).join('')}</div>` : '<p class="small muted">Aucune carte ne répond à ces critères.</p>'}`;
      } catch (err) {
        host.innerHTML = `<p class="small" style="color:var(--danger)">${esc(err.message)}</p>`;
      }
    }

    MM.el('#thPreviewBtn', root).addEventListener('click', preview);

    MM.el('#thSave', root).addEventListener('click', async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        const body = definition();
        const result = initial
          ? await MM.api.put(`/api/v2/themes/${initial.id}`, body)
          : await MM.api.post('/api/v2/themes', body);
        dialog.close();
        MM.toast.ok(initial ? 'Collection modifiée.' : 'Collection créée.');
        if (onSaved) onSaved(initial ? initial.id : result.id);
      } catch (err) {
        MM.toast.error(err.message);
        button.disabled = false;
      }
    });
  }

  window.MMThemes = { editor };
})();
