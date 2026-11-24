const status = document.querySelector('#dispatch-message');

async function post(path,body) {
  const response = await fetch(path, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || '交班提交失败');
  return data;
}

function bind(selector, submit) {
  const form = document.querySelector(selector);
  form?.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button');
    button.disabled = true;
    status.textContent = '正在核量…';
    try { await submit(form); }
    catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  });
}

bind('#archive', async form => {
  const files = Object.fromEntries(['tickets','receipts','stock'].map(id => [id,document.getElementById(id).value]));
  const values = Object.values(files);
  if (values.some(Boolean) && !values.every(Boolean)) throw new Error('请填全三份 CSV');
  const sample = form.dataset.sample;
  const ack = await post('/api/shifts', values.every(Boolean) ? files : {sample});
  location.assign('/ops/sign?' + new URLSearchParams({sample,batch:ack.batch}));
});

const pick = document.querySelector('#batch');
const hash = document.querySelector('#checksum');
const exportLink = document.querySelector('#export-slip');
pick?.addEventListener('change', () => { hash.value = ''; exportLink.hidden = true; });
bind('#signature', async () => {
  const batch = pick.value;
  const result = await post(`/api/shifts/${batch}/sign`,{});
  if (pick.value !== batch) return;
  hash.value = result.checksum;
  exportLink.href = `/api/shifts/${batch}/export`;
  exportLink.hidden = false;
  status.textContent = `证据已签发 · 待复核 ${result.open_items} 票`;
});

bind('#revocation', async form => {
  const batch = pick.value;
  const payload = {request_id: form.querySelector('#request-id').value.trim(),
                   reason: form.querySelector('#reason').value.trim()};
  if (!payload.request_id) throw new Error('撤销请求号必填，重试时请使用同一请求号');
  const result = await post(`/api/shifts/${batch}/revoke`, payload);
  const output = document.querySelector('#revoke-result');
  output.textContent = `${result.reused ? '重试命中原撤销' : '已撤销'} · ${result.revoked_at} · ${result.checksum.slice(0,12)}`;
  status.textContent = '该批次仅留档可查，不再具备结算资格';
  const chosen = pick.selectedOptions[0];
  if (chosen) { chosen.dataset.revoked = 'true'; chosen.textContent = chosen.textContent.replace(/待签发|已签发/, '已撤销'); }
});
