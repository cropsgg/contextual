import { Suspense } from "react";

export default function SettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center text-zinc-400">
          Loading…
        </div>
      }
    >
      {children}
    </Suspense>
  );
}
