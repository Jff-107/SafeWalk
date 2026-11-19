(() => {
  const key='safewalk-theme';
  function apply(theme){
    const dark=theme==='dark';document.body.classList.toggle('dark-mode',dark);
    document.querySelectorAll('[data-theme-toggle]').forEach(button=>{
      button.textContent=dark?'☀️ Modo claro':'🌙 Modo oscuro';
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
})();
