/** @type {import('next').NextConfig} */
// Navegador/compose: servidor standalone. Desktop (Tauri): exportação estática — a UI é 100% client-side
// (ADR-DESK-001), então o mesmo código vira arquivos estáticos empacotados no app.
const desktop = process.env.AIC_TARGET === "desktop";
const nextConfig = {
  output: desktop ? "export" : "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  // sempre definido: em produção vira constante "0" e o autoteste é eliminado do bundle (auditoria A20)
  env: { NEXT_PUBLIC_AIC_SELFTEST: process.env.NEXT_PUBLIC_AIC_SELFTEST === "1" ? "1" : "0" },
  ...(desktop ? { images: { unoptimized: true } } : {}),
};
export default nextConfig;
