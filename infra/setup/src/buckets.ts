/** Creates the private storage buckets if missing. Usage: pnpm infra:buckets */
import { CreateBucketCommand, HeadBucketCommand } from '@aws-sdk/client-s3';

import { loadEnv } from './env.ts';
import { bucketNames, s3Client } from './s3.ts';

loadEnv();

const s3 = s3Client();
for (const bucket of bucketNames()) {
  try {
    await s3.send(new HeadBucketCommand({ Bucket: bucket }));
    console.log(`buckets: ${bucket} already exists`);
  } catch (err) {
    const status = (err as { $metadata?: { httpStatusCode?: number } }).$metadata?.httpStatusCode;
    if (status !== 404) throw err;
    await s3.send(new CreateBucketCommand({ Bucket: bucket }));
    console.log(`buckets: created ${bucket} (private)`);
  }
}
