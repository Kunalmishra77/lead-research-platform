import { redirect } from 'next/navigation';

/** Jobs rather than orgs: staff open this area to find out why a run behaved as it did. */
export default function AdminIndex() {
  redirect('/admin/jobs');
}
