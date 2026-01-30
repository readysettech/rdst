/**
 * Types for Configure feature
 */

export interface ConfigureTarget {
  name: string;
  engine: string;
  has_password: boolean;
  is_default: boolean;
}

export interface ConfigureFormData {
  name: string;
  engine: string;
  host: string;
  port: number;
  database: string;
  user: string;
  password?: string;
  password_env?: string;
  tls?: boolean;
  read_only?: boolean;
}

export type ConfigureState = 'idle' | 'loading' | 'success' | 'error';

export interface ConfigureConnectionStatus {
  target: string;
  connected: boolean;
  error?: string;
  engine?: string;
}
