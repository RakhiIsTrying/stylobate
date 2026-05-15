import Link from "next/link";

export default function PortfolioLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Auth is enforced by `proxy.ts` (Next.js 16 proxy/middleware) which redirects
  // unauthenticated users to `/sign-in`. No layout-level gate is needed —
  // matches the `/chat` pattern.
  return (
    <main className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b px-4 py-3">
        <h1 className="text-sm font-semibold">Stylobate</h1>
        <nav className="flex gap-4 text-sm">
          <Link href="/chat" className="text-muted-foreground hover:text-foreground">
            Chat
          </Link>
          <Link href="/portfolio" className="font-semibold">
            Portfolio
          </Link>
        </nav>
      </header>
      <div className="flex-1 overflow-y-auto p-6">{children}</div>
    </main>
  );
}
