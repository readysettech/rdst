/**
 * Form component for configuring database targets
 */

import { useState, type ReactNode } from 'react';
import { Button } from '@rs/ui-new/button';
import { BaseInputText } from '@rs/ui-new/base-input-text';
import { BaseInputSelect } from '@rs/ui-new/base-input-select';
import { BaseInputSwitch } from '@rs/ui-new/base-input-switch';
import { Text } from '@rs/ui-new/text';
import { Icon } from '@rs/ui-new/icon';
import { Card } from '@rs/ui-new/card';
import { HStack, VStack } from '@rs/ui-new/stack';
import { Show } from '@rs/ui-new/show';
import { useDisclosure } from '@rs/ui-new/use-disclosure';
import type { ConfigureFormData } from '../../types/configure';

interface ConfigureFormProps {
  initialData?: Partial<ConfigureFormData>;
  onSubmit?: (data: ConfigureFormData) => void;
  onCancel?: () => void;
  isLoading?: boolean;
  /** Override the add-mode submit label (e.g. "Test & connect" on first run). */
  submitLabel?: string;
  /** Size of the primary submit button; first run uses a large hero CTA
   *  [VIS-022, VIS-035]. Defaults to `base` so other callers are unchanged. */
  submitSize?: 'base' | 'large';
}

const engineOptions = [
  { value: 'postgresql', label: 'PostgreSQL' },
  { value: 'mysql', label: 'MySQL' },
];

/**
 * A form label programmatically associated with its input via `htmlFor` — the
 * flagship-setup a11y fix (configure had 8 labels, 0 associated). `Text` does
 * not type `htmlFor`, so the association lives on a native `<label>` wrapping a
 * `Text` span. [USE-088]
 */
function FieldLabel({
  htmlFor,
  children,
}: {
  htmlFor: string;
  children: ReactNode;
}) {
  return (
    <label htmlFor={htmlFor} className="block mb-1.5">
      <Text as="span" level="label-small" className="text-content-layout-2">
        {children}
      </Text>
    </label>
  );
}

/**
 * Progressive-disclosure section (built on the shared `use-disclosure` hook).
 * The panel stays mounted and toggles visibility via `hidden`, so collapsing a
 * section never unmounts its fields (form state is preserved, and pre-filled
 * values survive a collapse). [VIS-114, USE-088]
 */
function Disclosure({
  id,
  title,
  subtitle,
  open,
  onToggle,
  children,
}: {
  id: string;
  title: string;
  subtitle?: string;
  open: boolean;
  onToggle: (open: boolean) => void;
  children: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-border-layout-1 overflow-hidden">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => onToggle(!open)}
        className="w-full flex items-center justify-between gap-3 px-4 py-3 text-left hover:bg-surface-layout-2/50 transition-colors cursor-pointer"
      >
        <VStack className="gap-0.5 items-start">
          <Text as="span" level="label-small" className="text-content-layout-1">
            {title}
          </Text>
          <Show when={!!subtitle}>
            <Text as="span" level="caption" className="text-content-layout-3">
              {subtitle}
            </Text>
          </Show>
        </VStack>
        <Icon
          name={open ? 'chevron-up' : 'chevron-down'}
          label={open ? 'Collapse' : 'Expand'}
          className="w-4 h-4 text-content-layout-3 shrink-0"
        />
      </button>
      <div id={id} hidden={!open} className="px-4 pb-4 pt-1">
        {children}
      </div>
    </div>
  );
}

export function defaultPasswordEnv(targetName: string): string {
  const normalized = targetName
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
  return normalized ? `RDST_${normalized}_PASSWORD` : '';
}

interface ParsedConnectionUrl {
  engine: string;
  host: string;
  port: number;
  database: string;
  user: string;
  password: string;
  tls: boolean;
}

function parseConnectionUrl(url: string): ParsedConnectionUrl | null {
  try {
    // Handle postgres:// as alias for postgresql://
    const normalizedUrl = url.replace(/^postgres:\/\//, 'postgresql://');

    // Check for supported protocols
    if (!normalizedUrl.startsWith('postgresql://') && !normalizedUrl.startsWith('mysql://')) {
      return null;
    }

    // Parse using URL API (replace protocol for parsing)
    const parsableUrl = normalizedUrl.replace(/^(postgresql|mysql):\/\//, 'http://');
    const parsed = new URL(parsableUrl);

    // Determine engine from original protocol
    const engine = normalizedUrl.startsWith('mysql://') ? 'mysql' : 'postgresql';
    const defaultPort = engine === 'mysql' ? 3306 : 5432;

    // Extract database from pathname (remove leading slash)
    const database = parsed.pathname.replace(/^\//, '');
    const params = parsed.searchParams;

    // Infer TLS/SSL from connection string query params
    let tls = false;
    if (engine === 'postgresql') {
      const sslMode = (params.get('sslmode') || '').toLowerCase();
      tls = sslMode === 'require' || sslMode === 'verify-ca' || sslMode === 'verify-full';
    } else {
      const ssl = (params.get('ssl') || '').toLowerCase();
      const sslMode = (params.get('ssl-mode') || '').toUpperCase();
      tls =
        ssl === 'true' ||
        ssl === '1' ||
        sslMode === 'REQUIRED' ||
        sslMode === 'VERIFY_CA' ||
        sslMode === 'VERIFY_IDENTITY';
    }

    return {
      engine,
      host: parsed.hostname || 'localhost',
      port: parsed.port ? Number.parseInt(parsed.port, 10) : defaultPort,
      database,
      user: parsed.username ? decodeURIComponent(parsed.username) : '',
      password: parsed.password ? decodeURIComponent(parsed.password) : '',
      tls,
    };
  } catch {
    return null;
  }
}

export function ConfigureForm({ initialData, onSubmit, onCancel, isLoading, submitLabel, submitSize = 'base' }: ConfigureFormProps) {
  const isAddMode = !initialData?.name;
  const [connectionUrl, setConnectionUrl] = useState('');
  const [urlError, setUrlError] = useState<string | null>(null);
  const [name, setName] = useState(initialData?.name || '');
  const [engine, setEngine] = useState(initialData?.engine || 'postgresql');
  const defaultPort =
    initialData?.port ??
    ((initialData?.engine || 'postgresql') === 'mysql' ? 3306 : 5432);
  const [host, setHost] = useState(initialData?.host || 'localhost');
  const [port, setPort] = useState(defaultPort);
  const [database, setDatabase] = useState(initialData?.database || '');
  const [user, setUser] = useState(initialData?.user || '');
  const [passwordEnv, setPasswordEnv] = useState(initialData?.password_env || '');
  const [password, setPassword] = useState('');
  const [passwordEnvCustomized, setPasswordEnvCustomized] = useState(
    Boolean(initialData?.password_env),
  );
  const [tls, setTls] = useState(initialData?.tls ?? false);
  const [readOnly, setReadOnly] = useState(initialData?.read_only ?? false);

  // "Connection details" holds the fields the connection needs, so it opens by
  // default; "Advanced" (TLS / read-only) stays collapsed until asked for. A
  // paste reveals both so the auto-filled values — including the inferred TLS —
  // are visible for review. [VIS-114, USE-067]
  const [detailsOpenState, setDetailsOpenState] = useState(true);
  const [detailsOpen, setDetailsOpen] = useDisclosure({
    open: detailsOpenState,
    onOpenChange: setDetailsOpenState,
  });
  const [advancedOpen, setAdvancedOpen] = useDisclosure({});

  const handleParseUrl = () => {
    setUrlError(null);
    const trimmedUrl = connectionUrl.trim();

    if (!trimmedUrl) {
      setUrlError('Please enter a connection URL');
      return;
    }

    const parsed = parseConnectionUrl(trimmedUrl);
    if (!parsed) {
      setUrlError('Invalid URL format. Expected: postgresql://user@host:port/database');
      return;
    }

    setEngine(parsed.engine);
    setHost(parsed.host);
    setPort(parsed.port);
    setDatabase(parsed.database);
    setUser(parsed.user);
    setPassword(parsed.password);
    setTls(parsed.tls);

    // Auto-generate name from database if not already set
    if (!name && parsed.database) {
      setName(parsed.database);
      if (!passwordEnvCustomized) {
        setPasswordEnv(defaultPasswordEnv(parsed.database));
      }
    }

    // Reveal the pre-filled fields (and the inferred TLS in Advanced) for review.
    setDetailsOpen(true);
    setAdvancedOpen(true);

    // Clear the URL field after successful parse
    setConnectionUrl('');
  };

  const isAddModePasswordValid =
    !isAddMode || (passwordEnv.trim().length > 0 && password.length > 0);
  const isValid = name && host && port && database && user && isAddModePasswordValid;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!isValid || !onSubmit) return;

    onSubmit({
      name,
      engine,
      host,
      port,
      database,
      user,
      password: password || undefined,
      password_env: passwordEnv || undefined,
      tls,
      read_only: readOnly,
    });
  };

  const handleCancel = () => {
    setName('');
    setEngine('postgresql');
    setHost('localhost');
    setPort(5432);
    setDatabase('');
    setUser('');
    setPasswordEnv('');
    setPassword('');
    setPasswordEnvCustomized(false);
    setTls(false);
    setReadOnly(false);
    onCancel?.();
  };

  return (
    <form onSubmit={handleSubmit}>
      <Card className="w-full">
        <Card.Header>
          <HStack className="gap-2 items-center">
            <Icon
              name={isAddMode ? 'add' : 'edit'}
              label={isAddMode ? 'Add' : 'Edit'}
              className="w-4 h-4 text-content-layout-3"
            />
            <Text level="label-medium" className="text-content-layout-1">
              {initialData?.name ? 'Edit Target' : 'New Target'}
            </Text>
          </HStack>
        </Card.Header>
      <Card.Content>
          <div className="space-y-5">
            {/* Quick Setup — the primary path. Hidden when editing a known
                connection (there's no string to paste). The parser itself stays
                intact for add mode. [USE-067, VIS-121] */}
            {isAddMode && (
              <div className="rounded-xl bg-surface-layout-2/50 p-4">
                <HStack className="gap-2 items-center mb-3">
                  <Icon name="connect" label="Quick setup" className="w-4 h-4 text-content-primary-soft" />
                  <label htmlFor="cfg-connection-url">
                    <Text as="span" level="label-small" className="text-content-primary-soft">
                      Quick Setup
                    </Text>
                  </label>
                </HStack>
                <div className="space-y-2">
                  <div className="flex gap-2">
                    <div className="flex-1">
                      <BaseInputText
                        id="cfg-connection-url"
                        name="connectionUrl"
                        value={connectionUrl}
                        onChange={(e) => {
                          setConnectionUrl(e.target.value);
                          setUrlError(null);
                        }}
                        placeholder="postgresql://user@host:5432/database"
                        disabled={isLoading}
                      />
                    </div>
                    <Button
                      variant="primary"
                      modifier="outline"
                      label="Parse"
                      type="button"
                      onClick={handleParseUrl}
                      disabled={isLoading || !connectionUrl.trim()}
                    />
                  </div>
                  <Show when={!!urlError}>
                    <Text level="body-small" className="text-content-negative-soft">
                      {urlError}
                    </Text>
                  </Show>
                  <Text level="caption" className="text-content-layout-3">
                    Paste a connection URL to auto-fill the form fields
                  </Text>
                </div>
              </div>
            )}

            {/* Name — always visible; it's the identity you'll pick the
                connection by. */}
            <div>
              <FieldLabel htmlFor="cfg-name">Target Name *</FieldLabel>
              <BaseInputText
                id="cfg-name"
                name="name"
                value={name}
                onChange={(e) => {
                  const nextName = e.target.value;
                  setName(nextName);
                  if (isAddMode && !passwordEnvCustomized) {
                    setPasswordEnv(defaultPasswordEnv(nextName));
                  }
                }}
                placeholder="my-database"
                disabled={isLoading || !!initialData?.name}
                required
              />
              <Show when={!isAddMode}>
                <Text level="caption" className="text-content-layout-3 mt-1">
                  The name can't be changed after a connection is created.
                </Text>
              </Show>
            </div>

            {/* Connection details — the manual field grid, deferred behind a
                disclosure that opens pre-filled after a paste. [USE-067, VIS-114] */}
            <Disclosure
              id="cfg-connection-details"
              title="Connection details"
              subtitle="Engine, host, port, database, user, password"
              open={detailsOpen}
              onToggle={setDetailsOpen}
            >
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <FieldLabel htmlFor="cfg-engine">Database Engine *</FieldLabel>
                    <BaseInputSelect
                      id="cfg-engine"
                      name="engine"
                      options={engineOptions}
                      value={engine}
                      onValueChange={setEngine}
                      disabled={isLoading}
                    />
                  </div>

                  <div>
                    <FieldLabel htmlFor="cfg-host">Host *</FieldLabel>
                    <BaseInputText
                      id="cfg-host"
                      name="host"
                      value={host}
                      onChange={(e) => setHost(e.target.value)}
                      placeholder="localhost"
                      disabled={isLoading}
                      required
                    />
                  </div>

                  <div>
                    <FieldLabel htmlFor="cfg-port">Port *</FieldLabel>
                    <BaseInputText
                      id="cfg-port"
                      name="port"
                      type="number"
                      value={String(port)}
                      onChange={(e) => setPort(Number(e.target.value) || 5432)}
                      placeholder="5432"
                      disabled={isLoading}
                      required
                    />
                  </div>

                  <div>
                    <FieldLabel htmlFor="cfg-database">Database *</FieldLabel>
                    <BaseInputText
                      id="cfg-database"
                      name="database"
                      value={database}
                      onChange={(e) => setDatabase(e.target.value)}
                      placeholder="myapp"
                      disabled={isLoading}
                      required
                    />
                  </div>

                  <div>
                    <FieldLabel htmlFor="cfg-user">User *</FieldLabel>
                    <BaseInputText
                      id="cfg-user"
                      name="user"
                      value={user}
                      onChange={(e) => setUser(e.target.value)}
                      placeholder="postgres"
                      disabled={isLoading}
                      required
                    />
                  </div>
                </div>

                <div>
                  <FieldLabel htmlFor="cfg-password">
                    Database Password {isAddMode ? '*' : ''}
                  </FieldLabel>
                  <BaseInputText
                    id="cfg-password"
                    name="password"
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder={isAddMode ? 'Enter database password' : 'Leave blank to keep current password'}
                    disabled={isLoading}
                    required={isAddMode}
                    autoComplete="new-password"
                  />
                  <Text level="caption" className="text-content-layout-3 mt-1">
                    Stored in your local secret store, never in the target configuration
                  </Text>
                </div>

                <div>
                  <FieldLabel htmlFor="cfg-password-env">
                    Password Environment Variable {isAddMode ? '*' : ''}
                  </FieldLabel>
                  <BaseInputText
                    id="cfg-password-env"
                    name="password_env"
                    value={passwordEnv}
                    onChange={(e) => {
                      setPasswordEnv(e.target.value);
                      setPasswordEnvCustomized(true);
                    }}
                    placeholder="RDST_MY_DATABASE_PASSWORD"
                    disabled={isLoading}
                    required={isAddMode}
                  />
                  <Text level="caption" className="text-content-layout-3 mt-1">
                    Advanced: the name RDST uses to look up this password
                  </Text>
                </div>
              </div>
            </Disclosure>

            {/* Advanced — TLS + read-only, collapsed until asked for. [VIS-114] */}
            <Disclosure
              id="cfg-advanced"
              title="Advanced"
              subtitle="TLS, read-only"
              open={advancedOpen}
              onToggle={setAdvancedOpen}
            >
              <div className="space-y-4">
                <div className="flex items-center justify-between rounded-lg bg-surface-layout-2/50 px-4 py-3">
                  <label htmlFor="cfg-tls" className="cursor-pointer">
                    <VStack className="gap-0.5 items-start">
                      <Text as="span" level="label-small" className="text-content-layout-1">
                        TLS / SSL
                      </Text>
                      <Text as="span" level="caption" className="text-content-layout-3">
                        Require encrypted connection
                      </Text>
                    </VStack>
                  </label>
                  <BaseInputSwitch
                    id="cfg-tls"
                    name="tls"
                    aria-label="TLS / SSL — require encrypted connection"
                    checked={tls}
                    onCheckedChange={setTls}
                    disabled={isLoading}
                  />
                </div>

                <div className="flex items-center justify-between rounded-lg bg-surface-layout-2/50 px-4 py-3">
                  <label htmlFor="cfg-read-only" className="cursor-pointer">
                    <VStack className="gap-0.5 items-start">
                      <Text as="span" level="label-small" className="text-content-layout-1">
                        Read Only
                      </Text>
                      <Text as="span" level="caption" className="text-content-layout-3">
                        Restrict to SELECT queries only
                      </Text>
                    </VStack>
                  </label>
                  <BaseInputSwitch
                    id="cfg-read-only"
                    name="read_only"
                    aria-label="Read Only — restrict to SELECT queries only"
                    checked={readOnly}
                    onCheckedChange={setReadOnly}
                    disabled={isLoading}
                  />
                </div>
              </div>
            </Disclosure>
          </div>
        </Card.Content>
        <Card.Footer>
          <HStack className="gap-3 justify-end w-full">
            <Button
              variant="primary"
              modifier="ghost"
              label="Cancel"
              onClick={handleCancel}
              disabled={isLoading}
              type="button"
            />
            <Button
              variant="rising"
              modifier="solid"
              size={submitSize}
              label={initialData?.name ? 'Update Target' : submitLabel ?? 'Add Target'}
              type="submit"
              loading={isLoading}
              disabled={!isValid}
            />
          </HStack>
        </Card.Footer>
      </Card>
    </form>
  );
}
