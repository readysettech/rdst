export function Main({ children }: { children: React.ReactNode }) {
  return (
    <main className="pl-64 h-[calc(100dvh-56px)] bg-surface-layout-2 overflow-y-auto">
      <div className="p-6 max-w-6xl container">{children}</div>
    </main>
  );
}
