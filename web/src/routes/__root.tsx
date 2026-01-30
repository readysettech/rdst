import { createRootRoute, Outlet } from '@tanstack/react-router';
import { Header } from '../layout/Header';
import { Sidebar } from '../layout/Sidebar';
import { Main } from '../layout/Main';
import { ConfigWarning } from '../components';

export const Route = createRootRoute({
  component: RootComponent,
});

function RootComponent() {
  return (
    <div className="relative bg-surface-layout-2 h-dvh overflow-hidden">
      <Header />
      <Sidebar />
      <Main>
        <ConfigWarning />
        <Outlet />
      </Main>
    </div>
  );
}
