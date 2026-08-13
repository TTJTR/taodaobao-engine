import { Redis } from "ioredis";

import type { JobRecord } from "./contracts.js";

export interface JobStore {
  connect(): Promise<void>;
  get(jobId: string): Promise<JobRecord | null>;
  set(job: JobRecord): Promise<boolean>;
  publishCancel(jobId: string): Promise<void>;
  subscribeCancel(handler: (jobId: string) => void): Promise<void>;
  ping(): Promise<void>;
  close(): Promise<void>;
}

export class RedisJobStore implements JobStore {
  private readonly command: Redis;
  private readonly subscriber: Redis;
  private readonly ttlSeconds: number;
  private readonly keyPrefix: string;
  private readonly cancelChannel: string;

  constructor(redisUrl: string, ttlSeconds = 86_400) {
    this.command = new Redis(redisUrl, { lazyConnect: true, maxRetriesPerRequest: 2 });
    this.subscriber = new Redis(redisUrl, { lazyConnect: true, maxRetriesPerRequest: 2 });
    this.ttlSeconds = ttlSeconds;
    this.keyPrefix = "open-enrich:job:";
    this.cancelChannel = "open-enrich:cancel";
  }

  async connect(): Promise<void> {
    if (this.command.status === "wait") await this.command.connect();
    if (this.subscriber.status === "wait") await this.subscriber.connect();
  }

  async get(jobId: string): Promise<JobRecord | null> {
    const raw = await this.command.get(this.key(jobId));
    return raw ? JSON.parse(raw) as JobRecord : null;
  }

  async set(job: JobRecord): Promise<boolean> {
    const result = await this.command.eval(
      `local current = redis.call('GET', KEYS[1])
       if current then
         local status = cjson.decode(current)['status']
         if status == 'completed' or status == 'partial' or status == 'failed' then
           return 0
         end
       end
       redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
       return 1`,
      1,
      this.key(job.id),
      JSON.stringify(job),
      String(this.ttlSeconds),
    );
    return result === 1;
  }

  async publishCancel(jobId: string): Promise<void> {
    await this.command.publish(this.cancelChannel, jobId);
  }

  async subscribeCancel(handler: (jobId: string) => void): Promise<void> {
    await this.subscriber.subscribe(this.cancelChannel);
    this.subscriber.on("message", (channel: string, jobId: string) => {
      if (channel === this.cancelChannel) handler(jobId);
    });
  }

  async ping(): Promise<void> {
    if (await this.command.ping() !== "PONG") throw new Error("Redis ping failed");
  }

  async close(): Promise<void> {
    await Promise.allSettled([this.subscriber.quit(), this.command.quit()]);
  }

  private key(jobId: string): string { return `${this.keyPrefix}${jobId}`; }
}
