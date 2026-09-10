window.mountOrderContacts = function(container, items, save, editable) {
  const el = (tag,text) => { const node=document.createElement(tag); node.textContent=text; return node; };
  container.replaceChildren(el('h3','本次工单联系记录'),el('p','仅用于本次售后。提交人、派单员和承派工程师可见，不同步客户主档。'));
  if (!items.length) container.append(el('p','旧工单未登记联系人姓名；原联系电话保留在工单信息中。'));
  items.forEach((c,i)=>{const latest=!items.slice(i+1).some(x=>x.purpose===c.purpose);container.append(el('p',`${c.purpose==='reporting'?'报修联系人':'现场联系人'}${latest?'（当前）':'（历史）'}：${c.name} · ${c.phone} · ${c.created_at ? new Date(c.created_at+'Z').toLocaleString('zh-CN') : ''}`));});
  if(!editable)return;
  const form=el('form',''),title=el('h4','补充 / 更换对接人'),purpose=el('select','');
  for(const [value,label] of [['onsite','现场联系人'],['reporting','报修联系人']]){const opt=el('option',label);opt.value=value;purpose.append(opt)}
  const name=el('input',''),phone=el('input',''),button=el('button','保存联系记录'),status=el('p','');
  purpose.className='form-select';name.className='form-input';phone.className='form-input';button.className='btn';
  purpose.setAttribute('aria-label','联系用途');name.placeholder='联系人姓名';name.setAttribute('aria-label','补充联系人姓名');name.required=true;name.maxLength=100;name.autocomplete='off';phone.placeholder='联系电话';phone.setAttribute('aria-label','补充联系电话');phone.type='tel';phone.required=true;phone.maxLength=50;phone.autocomplete='off';button.type='submit';status.setAttribute('role','status');
  form.append(title,purpose,name,phone,button,status);container.append(form);
  form.onsubmit=async e=>{e.preventDefault();if(button.disabled)return;if(!name.value.trim()||!phone.value.trim()){status.textContent='请填写联系人和电话';return;}button.disabled=true;try{const result=await save({purpose:purpose.value,name:name.value.trim(),phone:phone.value.trim()});window.mountOrderContacts(container,result.items,save,editable);}catch(err){status.textContent=err.message;button.disabled=false;}};
};
