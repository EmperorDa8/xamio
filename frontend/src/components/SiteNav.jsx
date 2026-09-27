/**
 * The one header every Xamio page uses — landing, auth and the signed-in app —
 * so moving between them never feels like switching products.
 */
export function Brand({ onClick, href = "#top" }) {
  return (
    <a
      className="x-brand"
      href={href}
      onClick={
        onClick
          ? (e) => {
              e.preventDefault();
              onClick();
            }
          : undefined
      }
    >
      <span className="x-brand-mark">X</span>
      <span className="x-brand-text">Xamio</span>
    </a>
  );
}

export default function SiteNav({ brand, links, actions }) {
  return (
    <header className="x-nav">
      {brand}
      {links && <nav className="x-nav-links">{links}</nav>}
      {actions && <span className="x-nav-actions">{actions}</span>}
    </header>
  );
}

export function SiteFooter({ children }) {
  return (
    <footer className="x-footer">
      <div className="x-footer-row">
        <Brand />
        <span className="x-footer-meta">Upload · Review · Alerts · Sync</span>
        <span className="x-footer-meta">© {new Date().getFullYear()} Xamio — never miss an exam</span>
      </div>
      {children}
    </footer>
  );
}
