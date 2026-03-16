const state = {
  catalog: [],
  cart: [],
  photoDataUrl: '',
  editingSale: null,
};

const rub = (v) => `${Number(v).toLocaleString('ru-RU')} ₽`;

const el = (id) => document.getElementById(id);

function switchTab(name) {
  el('tab-cart').classList.toggle('active', name === 'cart');
  el('tab-sales').classList.toggle('active', name === 'sales');
  el('view-cart').classList.toggle('active', name === 'cart');
  el('view-sales').classList.toggle('active', name === 'sales');
  el('cart-summary').classList.toggle('hidden', name !== 'cart');
}

async function fetchCatalog() {
  const q = encodeURIComponent(el('catalog-search').value.trim());
  const res = await fetch(`/api/catalog?q=${q}`);
  const data = await res.json();
  state.catalog = data.items;
  renderCatalog();
}

function renderCatalog() {
  const tbody = el('catalog-table').querySelector('tbody');
  tbody.innerHTML = '';
  for (const item of state.catalog) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${item.model}</td><td>${rub(item.price)}</td><td><button>+</button></td>`;
    tr.querySelector('button').onclick = () => {
      state.cart.push({ model: item.model, quantity: 1, size: (item.sizes?.[0] || 'M'), color: (item.colors?.[0] || 'Не указан'), price: item.price });
      renderCart();
    };
    tbody.appendChild(tr);
  }
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
      <td><input type="number" min="1" value="${item.quantity}"></td>
      <td><input value="${item.size}"></td>
      <td><input value="${item.color}"></td>
      <td><input type="number" min="0" value="${item.price}"></td>
      <td><button>×</button></td>`;
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
    tr.innerHTML = `<td>${new Date(sale.created_at).toLocaleString('ru-RU')}</td><td>${itemText}</td><td>${sale.discount_percent}%</td><td>${rub(sale.total_after)}</td><td><button>Ред.</button></td>`;
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
  const res = await fetch('/api/catalog/refresh', { method: 'POST' });
  const data = await res.json();
  state.catalog = data.items;
  renderCatalog();
  el('catalog-status').textContent = data.source === 'scraped'
    ? 'Каталог обновлен с unke.store.'
    : `Не удалось получить данные сайта, используется локальный каталог. ${data.warning || ''}`;
}

function init() {
  const today = new Date().toISOString().slice(0, 10);
  el('sales-date').value = today;

  el('tab-cart').onclick = () => switchTab('cart');
  el('tab-sales').onclick = () => { switchTab('sales'); fetchSales(); };
  el('catalog-search').oninput = fetchCatalog;
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

  fetchCatalog();
  fetchSales();
}

init();
