const screen = document.querySelector('#screen');
const routes = {purchase:'inventory/?screen=purchase',inventory:'inventory/?screen=inventory',urgent:'urgent/',orders:'orders/'};
function navigate(){
  const key = location.hash.slice(1).split('?')[0] || 'purchase';
  const selected = key in routes ? key : 'purchase';
  const path = '/boards/'+routes[selected];
  if(screen.getAttribute('src') !== path) screen.src=path;
  document.querySelectorAll('[data-screen]').forEach(a=>a.setAttribute('aria-current',a.dataset.screen===selected?'page':'false'));
  document.title='灯塔 · '+document.querySelector(`[data-screen="${selected}"]`).textContent;
}
function focus(enabled){document.body.classList.toggle('focused',enabled);document.querySelector('#exitFocus').hidden=!enabled;}
document.querySelector('#focus').onclick=()=>focus(true);
document.querySelector('#exitFocus').onclick=()=>focus(false);
document.addEventListener('keydown',e=>{if(e.key==='Escape') focus(false);});
window.addEventListener('message',e=>{if(e.origin===location.origin && e.source===screen.contentWindow){if(e.data==='exit-focus') focus(false);if(e.data==='access-required')document.querySelector('#accessDialog').showModal();}});
const dialog=document.querySelector('#accessDialog');
document.querySelector('#access').onclick=()=>dialog.showModal();
dialog.addEventListener('close',()=>{if(dialog.returnValue==='save'){sessionStorage.setItem('lighthouse-access',document.querySelector('#accessInput').value);document.querySelector('#accessInput').value='';screen.contentWindow.location.reload();}});
window.addEventListener('hashchange',navigate);navigate();
if(new URLSearchParams(location.search).get('focus')==='1') focus(true);
