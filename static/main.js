(function(){const carSelect=document.getElementById('car-select');if(!carSelect)return;
const sd=document.getElementById('start-date'),st=document.getElementById('start-time'),unit=document.getElementById('dur-unit'),val=document.getElementById('dur-val');
const money=n=>'₹'+Number(n||0).toLocaleString('en-IN',{maximumFractionDigits:0});const F=window.HOURLY_FACTOR||0.12;
const pad=n=>String(n).padStart(2,'0');
function update(){const opt=carSelect.options[carSelect.selectedIndex];const price=Number(opt?.dataset?.price||0);const label=opt?.textContent||'';const name=label.split(' · ')[0];
const hrs=unit.value==='hours';val.max=hrs?23:60;val.placeholder='';document.getElementById('dur-hint').textContent=hrs?'(1–23 hours)':'(1–60 days)';
let n=parseInt(val.value||'0',10);if(n>val.max){n=Number(val.max);val.value=n}
let total=0;if(price&&n>0)total=hrs?Math.min(price*F*n,price):price*n;
document.getElementById('summary-name').textContent=price?name:'Your vehicle awaits';
document.getElementById('summary-category').textContent=price?label.split(' · ')[1]||'Selected vehicle':'Select a car to see details';
document.getElementById('daily-rate').textContent=price?money(price):'—';
document.getElementById('hourly-rate').textContent=price?money(price*F)+'/hr':'—';
document.getElementById('duration').textContent=n>0?n+(hrs?' hour':' day')+(n===1?'':'s'):'Choose duration';
document.getElementById('total').textContent=money(total);
const ret=document.getElementById('return-at');
if(sd.value&&st.value&&n>0){const s=new Date(sd.value+'T'+st.value);const e=new Date(s.getTime()+(hrs?n*3600000:n*86400000));ret.textContent=e.toLocaleString('en-IN',{day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'})}else ret.textContent='Choose pick-up date & duration';
const img=opt?.dataset?.image;if(img)document.getElementById('summary-image').src=img}
[carSelect,sd,st,unit,val].forEach(el=>{el.addEventListener('change',update);el.addEventListener('input',update)});update();
document.querySelectorAll('.flash').forEach(el=>setTimeout(()=>{el.style.opacity='0';el.style.transition='opacity .4s';},5000));})();

(function(){const t=document.querySelector('meta[name=csrf]');if(!t)return;document.querySelectorAll('form[method=post]').forEach(f=>{if(!f.querySelector('[name=csrf_token]')){const i=document.createElement('input');i.type='hidden';i.name='csrf_token';i.value=t.content;f.appendChild(i)}});
const g=document.getElementById('car-grid');if(g){let cat='all';const q=document.getElementById('car-search');const run=()=>g.querySelectorAll('.car-card').forEach(c=>{c.style.display=(cat==='all'||c.dataset.cat===cat)&&c.dataset.name.includes(q.value.toLowerCase().trim())?'':'none'});q.addEventListener('input',run);document.getElementById('chips').addEventListener('click',e=>{const b=e.target.closest('.chip');if(!b)return;document.querySelectorAll('.chip').forEach(x=>x.classList.remove('active'));b.classList.add('active');cat=b.dataset.cat;run()})}
document.querySelectorAll('.history-card[data-paid="0"]').forEach(card=>{const id=card.dataset.booking;const iv=setInterval(async()=>{try{const r=await(await fetch('/payment/status/'+id)).json();if(r.payment_status&&r.payment_status.startsWith('Paid')){clearInterval(iv);location.reload()}}catch(e){}},5000)});
const sel=document.getElementById('car-select');if(sel)sel.addEventListener('change',()=>{const im=sel.options[sel.selectedIndex].dataset.image;if(im)document.getElementById('summary-image').src=im});})();
