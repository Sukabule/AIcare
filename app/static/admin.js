(() => {
  if (!document.querySelector('link[href*="workspace.css"]')) {
    const sheet = document.createElement('link');
    sheet.rel = 'stylesheet'; sheet.href = '/static/workspace.css?v=20261016';
    document.head.append(sheet);
  }
  document.body.classList.add('admin-page');
  const shapes = {
    home: '<path d="m3 10 9-7 9 7v10H3z"/><path d="M9 20v-7h6v7"/>',
    book: '<path d="M12 5c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Zm0 0v15"/>',
    review: '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V2h6v2M8 10l2 2 5-5M9 16h6"/>',
    chart: '<path d="M4 3v17h17M8 16V9m5 7V5m5 11v-6"/>',
    pulse: '<path d="M2 12h5l3-8 4 16 3-8h5"/>',
    chat: '<path d="M21 15a3 3 0 0 1-3 3H9l-6 3V6a3 3 0 0 1 3-3h12a3 3 0 0 1 3 3zM7 8h10M7 12h6"/>',
  };
  const icon = name => `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${shapes[name] || shapes.book}</svg>`;
  window.AIcareDesign = {icon};
  const nav = document.createElement('nav');
  nav.className = 'admin-nav'; nav.setAttribute('aria-label','后台导航');
  const brand = document.createElement('a'); brand.href = '/admin'; brand.className = 'brand';
  brand.innerHTML = '<span class="brand-symbol"><svg width="25" height="25" viewBox="0 0 32 32" fill="none" aria-hidden="true"><path d="M6 14V5l8 5h4l8-5v9c4 13-24 13-20 0Z" fill="currentColor"/><path d="M11 17h2m6 0h2m-6 4h2" stroke="#bfe8d9" stroke-width="2.5" stroke-linecap="round"/></svg></span><span><strong>AIcare</strong><small>客服工作台</small></span>';
  nav.append(brand);
  const entries = [['管理首页','/admin','home'],['知识库','/kb','book'],['审核队列','/review','review'],['RAG 评估','/rag-eval','chart'],['观测与成本','/observability','pulse'],['客服聊天','/','chat']];
  for (const [index,[name,path,symbol]] of entries.entries()) {
    if (index === 0 || index === 3 || index === 5) {
      const group = document.createElement('div'); group.className='nav-group';
      group.textContent = index === 0 ? '知识与服务' : index === 3 ? '质量与运行' : '对话入口';
      nav.append(group);
    }
    const a = document.createElement('a'); a.href = path;
    a.innerHTML = `<span class="nav-icon">${icon(symbol)}</span>`;
    const label = document.createElement('span'); label.textContent = name; a.append(label);
    if (location.pathname === path) a.setAttribute('aria-current','page');
    nav.append(a);
  }
  const footer = document.createElement('div'); footer.className='nav-footer';
  footer.innerHTML='<strong>本地开发工作区</strong>先核实知识，再交给客服回答。'; nav.append(footer);
  document.body.prepend(nav);
  const skip = document.createElement('a'); skip.className='skip-content'; skip.href='#main-content'; skip.textContent='跳到主要内容'; document.body.prepend(skip);
  const prepare = () => {
    const main=document.querySelector('main');
    if(main) {main.id='main-content'; main.tabIndex=-1;}
    const positionActive=()=>{
      if(!window.matchMedia('(max-width:900px)').matches) return;
      const active=nav.querySelector('[aria-current="page"]');
      if(active) nav.scrollLeft=Math.max(0,active.offsetLeft+active.offsetWidth-nav.clientWidth+18);
    };
    positionActive(); window.addEventListener('resize',positionActive);
  };
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded',prepare,{once:true}); else prepare();
})();

function mountAdminNav(path) {
  document.querySelectorAll('.admin-nav a').forEach(a=>{if(a.getAttribute('href')===path) a.setAttribute('aria-current','page');});
}
