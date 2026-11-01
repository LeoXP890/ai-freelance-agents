import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Commerce · Escritório",
  description: "Escritório virtual dos agentes — estado vindo do backend (DRY_RUN).",
};
export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#0b1020" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR">
      <body>{children}</body>
    </html>
  );
}
