(() => {
  const key='safewalk-theme';
  function apply(theme){
    const dark=theme==='dark';document.body.classList.toggle('dark-mode',dark);
    document.querySelectorAll('[data-theme-toggle]').forEach(button=>{
      button.textContent=dark?'Modo claro':'Modo oscuro';
      button.setAttribute('aria-label',dark?'Activar modo claro':'Activar modo oscuro');
      button.setAttribute('aria-pressed',String(dark));
    });
  }
  apply(localStorage.getItem(key)||'light');
  document.querySelectorAll('[data-theme-toggle]').forEach(button=>button.addEventListener('click',()=>{
    const theme=document.body.classList.contains('dark-mode')?'light':'dark';
    localStorage.setItem(key,theme);apply(theme);
  }));
  const sidebarButton=document.getElementById('sidebar-toggle');
  function setSidebar(hidden){
    document.body.classList.toggle('sidebar-hidden',hidden);
    if(sidebarButton){sidebarButton.textContent=hidden?'☰':'×';sidebarButton.setAttribute('aria-label',hidden?'Mostrar menú':'Ocultar menú');sidebarButton.title=hidden?'Mostrar menú':'Ocultar menú';sidebarButton.setAttribute('aria-expanded',String(!hidden));}
  }
  setSidebar(localStorage.getItem('safewalk-sidebar-hidden')==='true');
  sidebarButton?.addEventListener('click',()=>{
    const hidden=!document.body.classList.contains('sidebar-hidden');
    localStorage.setItem('safewalk-sidebar-hidden',String(hidden));setSidebar(hidden);
  });
  function updateNavigation() {
    const section = ['#alertas', '#reportes', '#registros'].includes(location.hash) ? location.hash : '';
    document.querySelectorAll('.sidebar nav a').forEach(link => {
      const target = new URL(link.href, location.href);
      const active = target.pathname === location.pathname &&
        (location.pathname === '/' ? target.hash === section : true);
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', section ? 'location' : 'page');
      else link.removeAttribute('aria-current');
    });
  }
  updateNavigation();
  window.addEventListener('hashchange', updateNavigation);
})();
