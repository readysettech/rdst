import { createRootRoute, Outlet } from '@tanstack/react-router';
import { cn } from '@rs/tailwind-base';
import { Header } from '../layout/Header';
import { Sidebar } from '../layout/Sidebar';
import { Main } from '../layout/Main';
import { ConfigWarning } from '../components';
import { isDesktopLinux, isDesktopMac } from '../lib/desktop';
import { EmailGate } from '../components/EmailGate';

export const Route = createRootRoute({
  component: RootComponent,
});

function RootComponent() {
  const isElectronMac = isDesktopMac();
  const isElectronLinux = isDesktopLinux();

  return (
    <div
      className={cn(
        'relative h-dvh overflow-hidden',
        isElectronMac
          ? 'm-2 rounded-2xl border border-border-layout-1/70 bg-surface-layout-2/35 backdrop-blur-xl shadow-[0_20px_48px_rgba(0,0,0,0.35)]'
          : 'bg-surface-layout-2',
      )}
    >
      <Header isElectronMac={isElectronMac} isElectronLinux={isElectronLinux} />
      <Sidebar isElectronMac={isElectronMac} />
      <Main isElectronMac={isElectronMac}>
        <ConfigWarning />
        <Outlet />
      </Main>
      <EmailGate />
    </div>
  );
}
