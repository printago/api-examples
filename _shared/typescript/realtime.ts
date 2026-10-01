/**
 * Connecting to Printago's real-time MQTT feed.
 *
 * Every example that listens for live data ships its own copy of this file.
 * Docs: https://docs.printago.io/api/authentication-and-quotas#real-time-data-with-mqtt
 */
import { randomBytes } from 'node:crypto';
import mqtt, { type MqttClient } from 'mqtt';

export interface RealtimeOptions {
  apiKey: string;
  /** Your store ID. It is the MQTT username. */
  storeId: string;
  /** Defaults to mqtts://realtime.printago.io:8883. */
  url?: string;
}

/**
 * Open one MQTT connection for this process.
 *
 * The client ID must be `apiclient_{keyId}_{suffix}`, where keyId is the first
 * 24 characters of the key. The suffix is generated once here and reused for
 * every reconnect: two connections sharing a client ID knock each other off
 * the broker in an endless loop.
 */
export function connectRealtime({ apiKey, storeId, url = 'mqtts://realtime.printago.io:8883' }: RealtimeOptions): MqttClient {
  const keyId = apiKey.slice(0, 24);
  const suffix = randomBytes(4).toString('hex');

  const client = mqtt.connect(url, {
    clientId: `apiclient_${keyId}_${suffix}`,
    username: storeId,
    password: apiKey,
    reconnectPeriod: 5_000,
    connectTimeout: 15_000,
  });

  client.on('connect', () => console.log(`[mqtt] connected to ${url}`));
  client.on('reconnect', () => console.log('[mqtt] reconnecting…'));
  client.on('error', (error) => console.error(`[mqtt] ${error.message}`));
  return client;
}

export function topics(storeId: string) {
  return {
    /** Every printer's live stats. Messages are retained, so you get the current state on subscribe. */
    allPrinterStats: `stores/${storeId}/printer-stats/#`,
    printerStats: (printerId: string) => `stores/${storeId}/printer-stats/${printerId}`,
    /** Created/updated/deleted events for one entity type, e.g. `print_jobs`. */
    entity: (entityType: string) => `stores/${storeId}/entities/${entityType}`,
  };
}

/** The live status of one printer, as published on `printer-stats/{printerId}`. */
export interface PrinterStat {
  printerId: string;
  storeId: string;
  /** Raw printer state, e.g. IDLE, PREPARE, RUNNING, PAUSE, FINISH, FAILED. */
  state?: string | null;
  isOnline: boolean;
  isAvailable?: boolean | null;
  receivedAt: string;
  /**
   * Printers re-report about once a minute. Past this time the stat is stale:
   * treat the printer as offline even if `isOnline` still says true.
   */
  expiresAt: string;
  data: {
    temperatures?: Record<string, { current?: number; target?: number }>;
    print?: {
      percent?: number;
      /** Minutes left. */
      timeRemaining?: number;
      currentLayer?: number;
      totalLayers?: number;
      taskName?: string;
      printJobId?: string;
      stage?: string;
    };
    health?: {
      errors: { code: string; message: string }[];
      warnings: { code: string; message: string }[];
      infos: { code: string; message: string }[];
    };
    camera?: {
      lastSnapshotAt?: number;
      snapshotsAvailable?: boolean;
    };
    [key: string]: unknown;
  };
}

/** An entity change event, as published on `entities/{entityType}`. */
export interface EntityEvent<T = Record<string, unknown>> {
  entityType: string;
  storeId: string;
  action: 'INSERT' | 'UPDATE' | 'DELETE';
  /** Keyed by entity ID. Values are the full new row, or null for DELETE. */
  entities: Record<string, T | null>;
  /** UPDATE only: for each entity ID, the fields that changed. */
  changes: Record<string, Record<string, { old: unknown; new: unknown }>>;
}

/** Parse a message payload. Returns null for an empty payload (a cleared retained message). */
export function parseMessage<T>(payload: Buffer): T | null {
  if (payload.length === 0) return null;
  try {
    return JSON.parse(payload.toString('utf8')) as T;
  } catch {
    return null;
  }
}
