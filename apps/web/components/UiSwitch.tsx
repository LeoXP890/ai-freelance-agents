"use client";
// App novo (desktop) por padrão; ?ui=legacy abre a interface anterior — rollback durante a migração (SDD v3 §10).
import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

const DesktopApp = dynamic(() => import("@/desktop/DesktopApp"), { ssr: false });
const OfficeApp = dynamic(() => import("@/components/OfficeApp"), { ssr: false });

export default function UiSwitch() {
  const [ui, setUi] = useState<"desktop" | "legacy" | null>(null);
  useEffect(() => { setUi(new URLSearchParams(window.location.search).get("ui") === "legacy" ? "legacy" : "desktop"); }, []);
  if (ui === null) return null;
  return ui === "legacy" ? <OfficeApp /> : <DesktopApp />;
}
