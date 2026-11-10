const origin = "https://sistema.appbarber.com.br";

// The legacy adapter only needs login and its static assets. Do not permit a
// dashboard, appointments, contracts or payments to become an interactive page.
export function routeDecision(raw, method, navigation = false) {
  let url;
  try { url = new URL(raw); } catch { return "deny"; }
  if (url.protocol !== "https:" || url.username || url.password || url.port) return "deny";
  if (url.origin === origin) {
    if (method === "GET" && navigation && ["/", "/index.php"].includes(url.pathname)) return "landing";
    if (method === "GET" && url.pathname === "/login.php") return "allow";
    if (method === "POST" && url.pathname === "/php/auth.php") return "allow";
    if (method === "GET" && !navigation && /\.(?:js|css|png|svg|jpg|gif|ico|webp|woff2?|ttf)$/i.test(url.pathname)) return "allow";
    return "deny";
  }
  const captcha = (
    ["www.google.com", "www.recaptcha.net", "www.gstatic.com"].includes(url.hostname) && url.pathname.startsWith("/recaptcha/") ||
    url.hostname === "challenges.cloudflare.com" && /^\/(turnstile|cdn-cgi\/challenge-platform)\//.test(url.pathname)
  );
  return captcha && ["GET", "POST"].includes(method) ? "allow" : "deny";
}

export function action(input) {
  if (input.action === "click" && Number.isInteger(input.x) && Number.isInteger(input.y) &&
      input.x >= 0 && input.x < 1024 && input.y >= 0 && input.y < 768) return {action:"click",x:input.x,y:input.y};
  if (input.action === "type_text" && typeof input.text === "string" && /^[\p{L}\p{N} @_.-]{1,80}$/u.test(input.text)) return {action:"type_text",text:input.text};
  if (input.action === "press" && ["Enter","Tab","Backspace","Escape","ArrowUp","ArrowDown","ArrowLeft","ArrowRight"].includes(input.key)) return {action:"press",key:input.key};
  throw new Error("invalid_action");
}
