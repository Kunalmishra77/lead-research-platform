import { S3Client } from '@aws-sdk/client-s3';

import { required } from './env.ts';

export function s3Client(): S3Client {
  return new S3Client({
    endpoint: required('S3_ENDPOINT'),
    region: required('S3_REGION'),
    forcePathStyle: true,
    credentials: {
      accessKeyId: required('S3_ACCESS_KEY_ID'),
      secretAccessKey: required('S3_SECRET_ACCESS_KEY'),
    },
  });
}

export function bucketNames(): string[] {
  return [required('S3_BUCKET_RAW'), required('S3_BUCKET_EXPORTS')];
}
