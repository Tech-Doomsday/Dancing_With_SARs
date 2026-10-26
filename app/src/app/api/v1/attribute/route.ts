/**
 * POST /api/v1/attribute — plan §12 API spec: "anomaly_id → posterior
 * probabilities, assigned cause, Shannon Entropy". Runs the SBAS pipeline
 * (deterministic; the anomaly ids are stable per pipeline version) and
 * returns the Bayesian attribution of the requested anomaly, including the
 * Bayes factor and the abstention state.
 */
import { NextResponse } from 'next/server';
import { z } from 'zod';
import { runSbasPipeline } from '@/lib/science/sbas-pipeline';
import { STACK_ID } from '@/lib/science/stacks';
import { DEFAULT_PARAMS } from '@/lib/science/types';

const bodySchema = z.object({
  anomaly_id: z.string().min(1),
  datasetId: z.string().optional().default(STACK_ID),
});

export async function POST(req: Request) {
  let body: z.infer<typeof bodySchema>;
  try {
    body = bodySchema.parse(await req.json());
  } catch (err) {
    return NextResponse.json({ ok: false, error: `Invalid request: ${(err as Error).message}` }, { status: 400 });
  }
  if (body.datasetId !== STACK_ID) {
    return NextResponse.json(
      { ok: false, error: `Only the demonstration stack (${STACK_ID}) is ingested in this deployment.` },
      { status: 409 },
    );
  }

  try {
    const result = runSbasPipeline(STACK_ID, DEFAULT_PARAMS, []);
    const anomaly = result.anomalies.find((a) => a.id === body.anomaly_id);
    if (!anomaly) {
      return NextResponse.json(
        {
          ok: false,
          error: `Unknown anomaly_id: ${body.anomaly_id}`,
          available: result.anomalies.map((a) => a.id),
        },
        { status: 404 },
      );
    }
    return NextResponse.json({
      ok: true,
      spec: '/api/v1/attribute (plan §12): posterior probabilities, assigned cause, Shannon entropy',
      anomalyId: anomaly.id,
      label: anomaly.label,
      areaKm2: anomaly.areaKm2,
      peakVelocityMPerYr: anomaly.peakVelocityMPerYr,
      features: anomaly.features,
      priors: anomaly.priors,
      posteriors: anomaly.bayes.posteriors,
      assignedCause: anomaly.bayes.assignedState,
      shannonEntropyNormalized: anomaly.bayes.entropy,
      bayesFactor: anomaly.bayes.bayesFactor,
      decisionRule: anomaly.bayes.decisionRule,
      evidence: anomaly.bayes.evidence,
      residuals: anomaly.residuals,
    });
  } catch (err) {
    return NextResponse.json({ ok: false, error: (err as Error).message }, { status: 500 });
  }
}
