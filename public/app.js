const state = {
  catalog: [],
  catalogLast5: [],
  cart: [],
  photoDataUrl: '',
  editingSale: null,
};

const rub = (v) => `${Number(v || 0).toLocaleString('ru-RU')} ₽`;
const el = (id) => document.getElementById(id);

function showSuggestions() {
  el('search-popup').classList.remove('d-none');
}

function hideSuggestions() {
  el('search-popup').classList.add('d-none');
}

function syncLayoutForFooter() {
  const footer = el('cart-summary');
  if (!footer) return;
  const h = Math.ceil(footer.getBoundingClientRect().height || 220);
  document.documentElement.style.setProperty('--cart-summary-height', `${h}px`);
}

function switchTab(name) {
  const isCart = name === 'cart';
  el('tab-cart').className = `btn ${isCart ? 'btn-dark' : 'btn-outline-dark'}`;
  el('tab-sales').className = `btn ${isCart ? 'btn-outline-dark' : 'btn-dark'}`;
  el('view-cart').classList.toggle('d-none', !isCart);
  el('view-sales').classList.toggle('d-none', isCart);
  el('cart-summary').classList.toggle('hidden', !isCart);
}

async function fetchCatalog(query = '', limit = '') {
  const q = encodeURIComponent(query);
  const l = limit ? `&limit=${limit}` : '';
  const res = await fetch(`/api/catalog?q=${q}${l}`);
  const data = await res.json();
  return data.items || [];
}

async function fetchSiteSearch(query = '') {
  const q = encodeURIComponent(query);
  const res = await fetch(`/api/catalog/site-search?q=${q}`);
  const data = await res.json();
  return data.items || [];
}

async function loadCatalogSnapshot() {
  state.catalog = await fetchCatalog('');
  state.catalogLast5 = await fetchCatalog('', 5);
}

function addToCart(item) {
  state.cart.push({
    model: item.model,
    quantity: 1,
    size: item.sizes?.[0] || 'M',
    color: item.colors?.[0] || 'Не указан',
    price: Number(item.price || 0),
  });
  renderCart();
}

function renderSearchPopup(items, query) {
  const list = el('site-search-results');
  const empty = el('site-search-empty');
  list.innerHTML = '';
  empty.classList.add('d-none');

  if (!items.length) {
    if (query.trim()) {
      empty.classList.remove('d-none');
      empty.innerHTML = `Товара "${query}" нет в выдаче сайта. Добавить новый?<div class="mt-2"><button id="add-manual-btn" class="btn btn-sm btn-warning">Добавить новый товар</button></div>`;
      el('add-manual-btn').onclick = () => openManualAdd(query.trim());
    } else {
      empty.classList.remove('d-none');
      empty.textContent = 'Начните вводить название товара для поиска на сайте.';
    }
    return;
  }

  items.forEach((item) => {
    const row = document.createElement('button');
    row.className = 'list-group-item list-group-item-action d-flex gap-2 align-items-start';
    const image = item.image ? `<img src="${item.image}" class="search-thumb" alt="${item.model}">` : '<div class="search-thumb search-thumb-empty">—</div>';
    const description = item.description ? `<div class="small text-secondary text-truncate-2">${item.description}</div>` : '';
    row.innerHTML = `${image}<div class="flex-grow-1 text-start"><div class="fw-semibold">${item.model}</div>${description}<div class="fw-semibold mt-1">${rub(item.price)}</div></div>`;
    row.onclick = () => {
      addToCart(item);
      el('catalog-search').value = '';
      hideSuggestions();
    };
    list.appendChild(row);
  });
}

function openManualAdd(query) {
  const model = prompt('Название модели', query || '');
  if (!model) return;
  const priceRaw = prompt('Цена (число)', '0');
  const price = Number(priceRaw || 0);
  createManualCatalogItem(model, Number.isFinite(price) ? price : 0);
}

async function createManualCatalogItem(model, price) {
  const res = await fetch('/api/catalog/manual', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model, price, sizes: ['Не указан'], colors: ['Не указан'] }),
  });
  const data = await res.json();
  if (!res.ok) {
    alert(data.error || 'Не удалось добавить товар');
    return;
  }
  await loadCatalogSnapshot();
  addToCart(data.item);
  hideSuggestions();
  el('catalog-status').textContent = 'Новый товар добавлен в каталог.';
}

function cartTotals() {
  const totalBefore = state.cart.reduce((sum, i) => sum + Number(i.price) * Number(i.quantity), 0);
  const discount = Number(el('discount').value || 0);
  const totalAfter = totalBefore * (1 - discount / 100);
  return { totalBefore, totalAfter };
}

function renderCart() {
  const tbody = el('cart-table').querySelector('tbody');
  tbody.innerHTML = '';
  state.cart.forEach((item, idx) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${item.model}</td>
      <td><input class="form-control form-control-sm input-mini" type="number" min="1" value="${item.quantity}"></td>
      <td><input class="form-control form-control-sm" value="${item.size}"></td>
      <td><input class="form-control form-control-sm" value="${item.color}"></td>
      <td><input class="form-control form-control-sm input-mini" type="number" min="0" value="${item.price}"></td>
      <td><button class="btn btn-sm btn-outline-danger">×</button></td>`;
    const [qty, size, color, price] = tr.querySelectorAll('input');
    qty.oninput = () => { item.quantity = Number(qty.value || 1); updateTotals(); };
    size.oninput = () => { item.size = size.value; };
    color.oninput = () => { item.color = color.value; };
    price.oninput = () => { item.price = Number(price.value || 0); updateTotals(); };
    tr.querySelector('button').onclick = () => { state.cart.splice(idx, 1); renderCart(); };
    tbody.appendChild(tr);
  });
  updateTotals();
}

function updateTotals() {
  const { totalBefore, totalAfter } = cartTotals();
  el('total-before').textContent = rub(totalBefore);
  el('total-after').textContent = rub(totalAfter);
}

async function createSale() {
  if (!state.cart.length) return alert('Добавьте товары в корзину');
  const payload = {
    items: state.cart,
    discount_percent: Number(el('discount').value || 0),
    note: el('sale-note').value,
    photo_data_url: state.photoDataUrl,
  };
  const res = await fetch('/api/sales', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  if (!res.ok) return alert('Ошибка сохранения');
  state.cart = [];
  state.photoDataUrl = '';
  el('sale-note').value = '';
  el('sale-photo').value = '';
  renderCart();
  switchTab('sales');
  await fetchSales();
}

async function fetchSales() {
  const date = el('sales-date').value;
  const search = el('sales-search').value.trim();
  const res = await fetch(`/api/sales?date=${encodeURIComponent(date)}&search=${encodeURIComponent(search)}`);
  const data = await res.json();
  renderSales(data.items, data.day_total);
}

function renderSales(items, dayTotal) {
  el('day-total').textContent = `Сумма за день: ${rub(dayTotal)}`;
  const tbody = el('sales-table').querySelector('tbody');
  tbody.innerHTML = '';
  items.forEach((sale) => {
    const tr = document.createElement('tr');
    const itemText = sale.items.map((i) => `${i.model} x${i.quantity}, ${i.size}, ${i.color}, ${rub(i.price)}`).join('<br>');
    tr.innerHTML = `<td>${new Date(sale.created_at).toLocaleString('ru-RU')}</td><td>${itemText}</td><td>${sale.discount_percent}%</td><td>${rub(sale.total_after)}</td><td><button class="btn btn-sm btn-outline-primary">Ред.</button></td>`;
    tr.querySelector('button').onclick = () => openEdit(sale);
    tbody.appendChild(tr);
  });
}

function openEdit(sale) {
  state.editingSale = sale;
  el('edit-datetime').value = sale.created_at.slice(0, 16);
  el('edit-discount').value = sale.discount_percent;
  el('edit-note').value = sale.note || '';
  el('edit-dialog').showModal();
}

async function saveEdit(e) {
  e.preventDefault();
  const sale = state.editingSale;
  if (!sale) return;
  const payload = {
    items: sale.items,
    created_at: new Date(el('edit-datetime').value).toISOString().slice(0, 19),
    discount_percent: Number(el('edit-discount').value || 0),
    note: el('edit-note').value,
    photo_data_url: sale.photo_data_url || '',
  };
  const res = await fetch(`/api/sales/${sale.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  if (res.ok) {
    el('edit-dialog').close();
    fetchSales();
  }
}

async function refreshCatalogFromSite() {
  el('catalog-status').textContent = 'Обновление каталога...';
  const res = await fetch('/api/catalog/refresh', { method: 'POST' });
  const data = await res.json();
  await loadCatalogSnapshot();
  el('catalog-status').textContent = data.source === 'scraped'
    ? `Каталог обновлен с unke.store. Найдено товаров: ${data.count}.`
    : `Не удалось получить данные сайта, используется локальный каталог. ${data.warning || ''}`;
}

let searchTimer = null;
async function handleCatalogInput() {
  const query = el('catalog-search').value.trim();
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    const items = query ? await fetchSiteSearch(query) : state.catalogLast5;
    renderSearchPopup(items, query);
  }, 220);
}

function init() {
  const today = new Date().toISOString().slice(0, 10);
  el('sales-date').value = today;

  el('tab-cart').onclick = () => switchTab('cart');
  el('tab-sales').onclick = () => { switchTab('sales'); fetchSales(); };
  el('catalog-search').onfocus = () => {
    showSuggestions();
    renderSearchPopup(state.catalogLast5, '');
  };
  el('catalog-search').oninput = handleCatalogInput;
  el('close-search-popup').onclick = hideSuggestions;
  el('search-popup').onclick = (e) => {
    if (e.target.id === 'search-popup') hideSuggestions();
  };

  el('sales-search').oninput = fetchSales;
  el('sales-date').onchange = fetchSales;
  el('discount').oninput = updateTotals;
  el('sell-btn').onclick = createSale;
  el('refresh-catalog').onclick = refreshCatalogFromSite;
  el('export-btn').onclick = () => { window.location.href = '/api/sales/export.xlsx'; };
  el('sale-photo').onchange = (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => { state.photoDataUrl = reader.result; };
    reader.readAsDataURL(file);
  };
  el('save-edit').onclick = saveEdit;

  loadCatalogSnapshot().catch(() => {
    el('catalog-status').textContent = 'Не удалось загрузить каталог.';
  });
  fetchSales();
  renderCart();
  switchTab('cart');
  syncLayoutForFooter();
  window.addEventListener('resize', syncLayoutForFooter);
}

init();
