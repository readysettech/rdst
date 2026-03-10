/**
 * Form component for configuring database targets
 */

import { useState } from 'react';
import { Button } from '@rs/ui-new/button';
import { BaseInputText } from '@rs/ui-new/base-input-text';
import { BaseInputSelect } from '@rs/ui-new/base-input-select';
import { BaseInputSwitch } from '@rs/ui-new/base-input-switch';
import { Text } from '@rs/ui-new/text';
import { Icon } from '@rs/ui-new/icon';
import { Card } from '@rs/ui-new/card';
import { HStack, VStack } from '@rs/ui-new/stack';
import { Show } from '@rs/ui-new/show';
import type { ConfigureFormData } from '../../types/configure';

interface ConfigureFormProps {
  initialData?: Partial<ConfigureFormData>;
  onSubmit?: (data: ConfigureFormData) => void;
  onCancel?: () => void;
  isLoading?: boolean;
}

const engineOptions = [
  { value: 'postgresql', label: 'PostgreSQL' },
  { value: 'mysql', label: 'MySQL' },
];

interface ParsedConnectionUrl {
  engine: string;
  host: string;
  port: number;
  database: string;
  user: string;
  hasPassword: boolean;
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
      hasPassword: !!parsed.password,
      tls,
    };
  } catch {
    return null;
  }
}

export function ConfigureForm({ initialData, onSubmit, onCancel, isLoading }: ConfigureFormProps) {
  const isAddMode = !initialData?.name;
  const [connectionUrl, setConnectionUrl] = useState('');
  const [urlError, setUrlError] = useState<string | null>(null);
  const [name, setName] = useState(initialData?.name || '');
  const [engine, setEngine] = useState(initialData?.engine || 'postgresql');
  const [host, setHost] = useState(initialData?.host || 'localhost');
  const [port, setPort] = useState(initialData?.port || 5432);
  const [database, setDatabase] = useState(initialData?.database || '');
  const [user, setUser] = useState(initialData?.user || '');
  const [passwordEnv, setPasswordEnv] = useState(initialData?.password_env || '');
  const [tls, setTls] = useState(initialData?.tls ?? false);
  const [readOnly, setReadOnly] = useState(initialData?.read_only ?? false);

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
    setTls(parsed.tls);

    // Auto-generate name from database if not already set
    if (!name && parsed.database) {
      setName(parsed.database);
    }

    // Clear the URL field after successful parse
    setConnectionUrl('');
  };

  const isAddModePasswordValid = !isAddMode || passwordEnv.trim().length > 0;
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
    setTls(false);
    setReadOnly(false);
    onCancel?.();
  };

  return (
    <form onSubmit={handleSubmit}>
      <Card className="w-full">
        <Card.Header>
          <HStack className="gap-2 items-center">
            <Icon name="add" label="Add" className="w-4 h-4 text-content-layout-3" />
            <Text level="label-medium" className="text-content-layout-1">
              {initialData?.name ? 'Edit Target' : 'New Target'}
            </Text>
          </HStack>
        </Card.Header>
      <Card.Content>
          <div className="space-y-6">
            {/* Connection URL */}
            <div className="rounded-xl bg-surface-layout-2/50 p-4">
              <HStack className="gap-2 items-center mb-3">
                <Icon name="connect" label="Quick setup" className="w-4 h-4 text-content-primary-soft" />
                <Text level="label-small" className="text-content-primary-soft">
                  Quick Setup
                </Text>
              </HStack>
              <div className="space-y-2">
                <div className="flex gap-2">
                  <div className="flex-1">
                    <BaseInputText
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

            {/* Basic Information */}
            <div>
              <HStack className="gap-2 items-center mb-4">
                <Icon name="info" label="Basic" className="w-4 h-4 text-content-layout-3" />
                <Text level="label-small" className="text-content-layout-2 uppercase tracking-wider">
                  Basic Information
                </Text>
              </HStack>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <Text as="label" level="label-small" className="text-content-layout-2 block mb-1.5">
                    Target Name *
                  </Text>
                  <BaseInputText
                    name="name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="my-database"
                    disabled={isLoading || !!initialData?.name}
                    required
                  />
                </div>

                <div>
                  <Text as="label" level="label-small" className="text-content-layout-2 block mb-1.5">
                    Database Engine *
                  </Text>
                  <BaseInputSelect
                    name="engine"
                    options={engineOptions}
                    value={engine}
                    onValueChange={setEngine}
                    disabled={isLoading}
                  />
                </div>
              </div>
            </div>

            {/* Connection Details */}
            <div>
              <HStack className="gap-2 items-center mb-4">
                <Icon name="database" label="Connection" className="w-4 h-4 text-content-layout-3" />
                <Text level="label-small" className="text-content-layout-2 uppercase tracking-wider">
                  Connection
                </Text>
              </HStack>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <Text as="label" level="label-small" className="text-content-layout-2 block mb-1.5">
                    Host *
                  </Text>
                  <BaseInputText
                    name="host"
                    value={host}
                    onChange={(e) => setHost(e.target.value)}
                    placeholder="localhost"
                    disabled={isLoading}
                    required
                  />
                </div>

                <div>
                  <Text as="label" level="label-small" className="text-content-layout-2 block mb-1.5">
                    Port *
                  </Text>
                  <BaseInputText
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
                  <Text as="label" level="label-small" className="text-content-layout-2 block mb-1.5">
                    Database *
                  </Text>
                  <BaseInputText
                    name="database"
                    value={database}
                    onChange={(e) => setDatabase(e.target.value)}
                    placeholder="myapp"
                    disabled={isLoading}
                    required
                  />
                </div>

                <div>
                  <Text as="label" level="label-small" className="text-content-layout-2 block mb-1.5">
                    User *
                  </Text>
                  <BaseInputText
                    name="user"
                    value={user}
                    onChange={(e) => setUser(e.target.value)}
                    placeholder="postgres"
                    disabled={isLoading}
                    required
                  />
                </div>
              </div>
            </div>

            {/* Security */}
            <div>
              <HStack className="gap-2 items-center mb-4">
                <Icon name="key" label="Security" className="w-4 h-4 text-content-layout-3" />
                <Text level="label-small" className="text-content-layout-2 uppercase tracking-wider">
                  Security
                </Text>
              </HStack>
              <div className="space-y-4">
                <div>
                  <Text as="label" level="label-small" className="text-content-layout-2 block mb-1.5">
                    Password Environment Variable {isAddMode ? '*' : ''}
                  </Text>
                  <BaseInputText
                    name="password_env"
                    value={passwordEnv}
                    onChange={(e) => setPasswordEnv(e.target.value)}
                    placeholder="DB_PASSWORD"
                    disabled={isLoading}
                    required={isAddMode}
                  />
                  <Text level="caption" className="text-content-layout-3 mt-1">
                    Name of environment variable containing the password
                  </Text>
                </div>

                <div className="flex items-center justify-between rounded-lg bg-surface-layout-2/50 px-4 py-3">
                  <VStack className="gap-0.5 items-start">
                    <Text level="label-small" className="text-content-layout-1">
                      TLS / SSL
                    </Text>
                    <Text level="caption" className="text-content-layout-3">
                      Require encrypted connection
                    </Text>
                  </VStack>
                  <BaseInputSwitch
                    name="tls"
                    checked={tls}
                    onCheckedChange={setTls}
                    disabled={isLoading}
                  />
                </div>

                <div className="flex items-center justify-between rounded-lg bg-surface-layout-2/50 px-4 py-3">
                  <VStack className="gap-0.5 items-start">
                    <Text level="label-small" className="text-content-layout-1">
                      Read Only
                    </Text>
                    <Text level="caption" className="text-content-layout-3">
                      Restrict to SELECT queries only
                    </Text>
                  </VStack>
                  <BaseInputSwitch
                    name="read_only"
                    checked={readOnly}
                    onCheckedChange={setReadOnly}
                    disabled={isLoading}
                  />
                </div>
              </div>
            </div>
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
              label={initialData?.name ? 'Update Target' : 'Add Target'}
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
