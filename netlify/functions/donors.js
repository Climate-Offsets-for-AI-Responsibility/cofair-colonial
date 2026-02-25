import { neon } from "@netlify/neon";

const sql = neon();

export async function handler() {
  const rows = await sql`
    SELECT * FROM staging.stg_donors LIMIT 50
  `;

  return {
    statusCode: 200,
    body: JSON.stringify(rows),
  };
}