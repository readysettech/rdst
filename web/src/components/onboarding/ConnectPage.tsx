import { useNavigate, useRouter } from '@tanstack/react-router';
import { useQueryClient } from '@tanstack/react-query';
import { Text } from '@rs/ui-new/text';
import { Icon } from '@rs/ui-new/icon';
import { HStack, VStack } from '@rs/ui-new/stack';
import { toast } from '@rs/ui-new/use-toast';
import { ConfigureForm } from '../configure';
import { useConfigure } from '../../lib/useConfigure';
import { useOnboarding } from '../../lib/useOnboarding';
import type { ConfigureFormData } from '../../types/configure';

/**
 * First-run "Connect your database" — a single, exitable page that replaces the
 * four-step `fixed inset-0` wizard (onboarding-and-first-run). One job: point
 * RDST at a database. The AI key is deferred to just-in-time (never asked here),
 * the demo is offered as a zero-setup escape hatch, and the page is a normal
 * route inside the app shell — skippable, never a takeover.
 * [USE-006, USE-008, USE-068, USE-050/052, VIS-011, VIS-116]
 */
export function ConnectPage({ redirectTo }: { redirectTo?: string }) {
  const navigate = useNavigate();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { addTarget, setDefaultTarget, loading } = useConfigure();
  const { completeInit } = useOnboarding();

  const leave = () => {
    // Return to where the user was headed when routed here, else Home.
    if (redirectTo && redirectTo !== '/onboarding') {
      router.history.push(redirectTo);
    } else {
      navigate({ to: '/' });
    }
  };

  const finishToHome = () => {
    queryClient.setQueryData(
      ['init-status'],
      (previous: { initialized?: boolean } | undefined) =>
        previous ? { ...previous, initialized: true } : previous,
    );
    queryClient.invalidateQueries({ queryKey: ['init-status'] });
    queryClient.invalidateQueries({ queryKey: ['status'] });
    leave();
  };

  const handleSubmit = async (data: ConfigureFormData) => {
    try {
      await addTarget(data);
      await setDefaultTarget(data.name);
      await completeInit();
    } catch {
      // useConfigure surfaces the failure inline; stay on the page so the
      // user can fix the connection details instead of dead-ending.
      return;
    }
    toast({ title: `Connected to ${data.name}`, variant: 'positive' });
    finishToHome();
  };

  const skip = () => {
    // Already-configured returning users (or "I'll do it later") can leave.
    leave();
  };

  return (
    <div className="min-h-dvh w-full overflow-y-auto bg-surface-layout-1">
      {/* Utility row: brand mark + exit */}
      <HStack className="justify-between items-center px-6 py-4">
        <HStack className="gap-2 items-center">
          <Icon name="speedometer" label="RDST" className="w-5 h-5 text-content-primary-soft" />
          <Text level="label-medium" className="text-content-layout-1">
            RDST
          </Text>
        </HStack>
        <button
          type="button"
          onClick={skip}
          className="text-content-layout-3 hover:text-content-layout-1 transition-colors text-sm inline-flex items-center gap-1"
        >
          Skip for now
          <Icon name="arrow-right" label="" className="w-3.5 h-3.5" />
        </button>
      </HStack>

      <div className="mx-auto w-full max-w-xl px-6 pb-16 pt-8">
        <VStack className="gap-2 items-start mb-6">
          <Text as="h1" level="headline-2" className="text-content-layout-1">
            Connect your database
          </Text>
          <Text level="body-medium" className="text-content-layout-3 leading-relaxed">
            RDST — the Readyset Data &amp; SQL Toolkit. Point it at your Postgres
            or MySQL to find slow queries, health issues, and caching wins.
          </Text>
        </VStack>

        <ConfigureForm
          onSubmit={handleSubmit}
          isLoading={loading}
          submitLabel="Test & connect"
        />

        <VStack className="gap-3 items-center mt-6">
          <button
            type="button"
            onClick={() => navigate({ to: '/demo' })}
            className="text-content-primary-soft hover:underline text-sm inline-flex items-center gap-1"
          >
            Just exploring? Try the live demo — no database needed
            <Icon name="arrow-right" label="" className="w-3.5 h-3.5" />
          </button>
          <Text level="caption" className="text-content-layout-3 text-center">
            You can add an AI key later, only when a feature needs it.
          </Text>
        </VStack>
      </div>
    </div>
  );
}
