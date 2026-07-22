/* shell.js — inyecta la barra de navegación unificada "AZC Accesos".
   Incluir en cualquier página con: <link rel="stylesheet" href="/portal/shell.css">
   y <script src="/portal/shell.js"></script>. Muestra solo las pestañas
   habilitadas por el permiso del usuario (GET /auth/me). */
(async function () {
  const NAV = [
    { cap: "ver_eventos",             href: "/analytics/",              label: "Eventos" },
    { cap: "ver_dashboard",           href: "/analytics/dashboard.html", label: "Dashboard" },
    { cap: "editar_tarjetas",         href: "/portal/cards.html",       label: "Tarjetas" },
    { cap: "editar_horarios",         href: "/schedules/",              label: "Horarios" },
    { cap: "gestionar_controladores", href: "/schedules/",              label: "Controladores" },
    { cap: "publicar_acl",            href: "/schedules/",              label: "Publicar" },
    { cap: "ver_panel",               href: "/",                        label: "Panel" },
    { cap: "gestionar_usuarios",      href: "/portal/users.html",       label: "Usuarios" },
  ];

  let me;
  try {
    const r = await fetch("/auth/me");
    if (!r.ok) { location.href = "/portal/login.html"; return; }
    me = await r.json();
  } catch (e) { return; }

  const has = (c) => me.caps.includes("*") || me.caps.includes(c);
  const path = location.pathname.replace(/\/+$/, "") || "/";

  const bar = document.createElement("div");
  bar.className = "azc-shell";

  const brand = document.createElement("a");
  brand.className = "azc-brand";
  brand.href = "/portal/";
  brand.textContent = "AZC Accesos";
  bar.appendChild(brand);

  const nav = document.createElement("nav");
  nav.className = "azc-nav";
  const seen = new Set();
  for (const n of NAV) {
    if (!has(n.cap) || seen.has(n.label)) continue;
    seen.add(n.label);
    const a = document.createElement("a");
    a.className = "azc-tab";
    a.href = n.href;
    a.textContent = n.label;
    const base = n.href.replace(/\/+$/, "") || "/";
    if (path === base || (base !== "/" && path.startsWith(base))) a.classList.add("active");
    nav.appendChild(a);
  }
  bar.appendChild(nav);

  const user = document.createElement("span");
  user.className = "azc-user";
  const name = document.createElement("span");
  name.textContent = (me.name || me.username) + "";
  const logout = document.createElement("a");
  logout.href = "#";
  logout.textContent = "Salir";
  logout.onclick = async (e) => {
    e.preventDefault();
    await fetch("/auth/logout", { method: "POST" });
    location.href = "/portal/login.html";
  };
  user.appendChild(name);
  user.appendChild(logout);
  bar.appendChild(user);

  document.body.insertBefore(bar, document.body.firstChild);
})();
