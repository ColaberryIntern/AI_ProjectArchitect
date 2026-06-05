/**
 * sendOperatorReviewEmail.js
 *
 * Reusable email-review-loop helper for the Operator 0-5 kickoff build.
 *
 * Sends Ali a styled HTML email with a visual artifact + 4 mailto: one-click
 * response buttons (Approve / Approve-with-comments / Request changes / STOP).
 *
 * Routed through the production Mandrill SMTP container per
 * docs/specs/operator-00-kickoff.md and the email-send-capability-handoff.md.
 *
 * USAGE (called by Claude Code; not by humans):
 *
 *   REVIEW_PAYLOAD=/app/tmp/op1-review-v01-payload.json node sendOperatorReviewEmail.js
 *
 * The payload JSON file is staged into the container via `docker compose cp`
 * just like the script itself. Schema below.
 *
 * PAYLOAD SCHEMA:
 *   {
 *     "spec_id": "operator-01",                       // for subject + tracking
 *     "spec_title": "Per-user CLAUDE.md scaffold",    // human-readable title
 *     "version": "v01",                                // increment per iteration
 *     "bc_ticket_url": "https://app.basecamp.com/...", // active BC ticket
 *     "spec_file_url": "https://github.com/...",       // spec on GitHub
 *     "summary_paragraph": "<one-paragraph summary of what's being reviewed>",
 *     "artifact": {
 *       "kind": "image" | "html",
 *       "filename": "/app/tmp/op1-review-v01.png",   // path inside container
 *       "alt_text": "screenshot of the assembled context banner"
 *     },
 *     "review_questions": [                            // optional, for inline checklist
 *       { "id": "q1", "text": "Does the org policy header read clearly?" },
 *       { "id": "q2", "text": "Are the 4 layers visually distinct enough?" }
 *     ]
 *   }
 */

const fs = require('fs');
const path = require('path');
const { createTransport } = require('nodemailer');

const REVIEW_REPLY_ADDR = 'ali@colaberry.com'; // replies land in Ali's inbox; Claude polls via Gmail MCP
const REPLY_TO_ADDR = REVIEW_REPLY_ADDR;       // intentional: same address; Gmail MCP filters by subject

// === Signature constants (verbatim from email-send-capability-handoff.md §6) ===

const HTML_SIGNATURE = `<table cellpadding="0" cellspacing="0" border="0" style="font-family: Aptos, Arial, sans-serif; font-size: 14px; color: #2d3748; border-left: 3px solid #1a365d; padding-left: 14px; margin-top: 20px;">
  <tr><td>
    <div style="font-weight: 700; font-size: 16px; color: #1a365d;">Ali Muwwakkil</div>
    <div style="color: #2b6cb0; font-weight: 600;">Managing Director / AI Systems Architect</div>
    <div style="color: #718096;">Colaberry Inc.</div>
    <div style="margin-top: 10px; color: #2d3748;">&#128205; 200 Chisholm Place, Suite 200 &middot; Plano, TX 75075</div>
    <div style="color: #2d3748;">&#9993; <a href="mailto:ali@colaberry.com" style="color: #2b6cb0; text-decoration: none;">ali@colaberry.com</a> &nbsp; &#127760; <a href="https://enterprise.colaberry.ai" style="color: #2b6cb0; text-decoration: none;">enterprise.colaberry.ai</a></div>
    <div style="margin-top: 14px;">
      <a href="https://advisor.colaberry.ai/advisory" style="display: inline-block; background: #2b6cb0; color: #ffffff; padding: 9px 18px; border-radius: 20px; text-decoration: none; font-weight: 600;">&#128640; Design Your AI Organization</a>
    </div>
  </td></tr>
</table>`;

const PLAIN_SIGNATURE = `Ali Muwwakkil
Managing Director / AI Systems Architect
Colaberry Inc.

200 Chisholm Place, Suite 200, Plano, TX 75075
ali@colaberry.com  |  enterprise.colaberry.ai
Design Your AI Organization: https://advisor.colaberry.ai/advisory`;

// === Helper: build mailto: link with prefilled subject + body ===

function buildMailto(toAddr, subject, body) {
  const params = new URLSearchParams({ subject, body });
  return `mailto:${toAddr}?${params.toString()}`;
}

// === Helper: render the 4 response buttons as inline HTML ===

function renderResponseButtons(specId, version, specTitle, bcTicketUrl) {
  const subjBase = `Re: [${specId} Review ${version}] ${specTitle}`;

  const approveSubj = `${subjBase} - APPROVED`;
  const approveBody = `Approved. Proceed to ship.\n\n(BC ticket: ${bcTicketUrl})`;

  const commentsSubj = `${subjBase} - COMMENTS`;
  const commentsBody = `Approve with comments. Notes below:\n\n[ Fill in your notes here ]\n\n(BC ticket: ${bcTicketUrl})`;

  const changesSubj = `${subjBase} - CHANGES`;
  const changesBody = `Request changes. Notes:\n\n[ What needs to change ]\n\n(BC ticket: ${bcTicketUrl})`;

  const stopSubj = `${subjBase} - STOP`;
  const stopBody = `Halt the review loop on this spec. Reason:\n\n[ Optional reason ]\n\n(BC ticket: ${bcTicketUrl})`;

  const btnBase =
    'display: inline-block; padding: 11px 18px; margin: 4px 6px 4px 0; border-radius: 6px; font-family: Aptos, Arial, sans-serif; font-size: 14px; font-weight: 600; text-decoration: none; color: #ffffff;';

  return `
<div style="margin: 20px 0; padding: 18px; border: 2px solid #e2e8f0; border-radius: 8px; background: #f7fafc;">
  <div style="font-weight: 700; color: #1a365d; font-size: 14px; margin-bottom: 12px;">
    Review action (one click sends your response):
  </div>
  <a href="${buildMailto(REVIEW_REPLY_ADDR, approveSubj, approveBody)}" style="${btnBase} background: #15803d;">
    &#128994; Approve this version
  </a>
  <a href="${buildMailto(REVIEW_REPLY_ADDR, commentsSubj, commentsBody)}" style="${btnBase} background: #d4a017;">
    &#128993; Approve with comments
  </a>
  <a href="${buildMailto(REVIEW_REPLY_ADDR, changesSubj, changesBody)}" style="${btnBase} background: #b91c1c;">
    &#128308; Request changes
  </a>
  <a href="${buildMailto(REVIEW_REPLY_ADDR, stopSubj, stopBody)}" style="${btnBase} background: #4a5568;">
    &#9899; Stop the loop
  </a>
  <div style="font-size: 12px; color: #718096; margin-top: 10px;">
    Each button opens your email client with a prefilled reply. Edit the body if needed, then hit Send.
  </div>
</div>`;
}

// === Helper: render review questions as a checklist ===

function renderReviewQuestions(questions) {
  if (!questions || questions.length === 0) return '';
  const items = questions
    .map(
      q => `<li style="margin: 6px 0;"><strong>${q.id}:</strong> ${q.text}</li>`
    )
    .join('');
  return `
<div style="margin: 20px 0; padding: 16px; background: #fffbea; border-left: 4px solid #d4a017;">
  <div style="font-weight: 700; color: #7d4e00; margin-bottom: 8px;">Specific questions for this review:</div>
  <ul style="margin: 0; padding-left: 22px; color: #2d3748;">${items}</ul>
</div>`;
}

// === Main ===

async function main() {
  const payloadPath = process.env.REVIEW_PAYLOAD;
  if (!payloadPath) {
    console.error('REVIEW_PAYLOAD env var required');
    process.exit(1);
  }

  const payload = JSON.parse(fs.readFileSync(payloadPath, 'utf8'));
  const {
    spec_id,
    spec_title,
    version,
    bc_ticket_url,
    spec_file_url,
    summary_paragraph,
    artifact,
    review_questions,
  } = payload;

  const subject = `[${spec_id} Review ${version}] ${spec_title}`;

  // Attachment + inline-image setup
  const attachments = [];
  let artifactHtml = '';

  if (artifact && artifact.kind === 'image' && fs.existsSync(artifact.filename)) {
    const cid = `artifact-${spec_id}-${version}@colaberry.com`;
    attachments.push({
      filename: path.basename(artifact.filename),
      content: fs.readFileSync(artifact.filename),
      contentType: 'image/png',
      cid,
    });
    artifactHtml = `
<div style="margin: 20px 0; text-align: center;">
  <img src="cid:${cid}" alt="${artifact.alt_text || 'review artifact'}" style="max-width: 720px; border: 1px solid #e2e8f0; border-radius: 6px;" />
  <div style="font-size: 12px; color: #718096; margin-top: 8px;">${artifact.alt_text || ''}</div>
</div>`;
  } else if (artifact && artifact.kind === 'html' && fs.existsSync(artifact.filename)) {
    const inlineHtml = fs.readFileSync(artifact.filename, 'utf8');
    artifactHtml = `
<div style="margin: 20px 0; padding: 16px; border: 1px solid #e2e8f0; border-radius: 6px; background: #ffffff;">
  ${inlineHtml}
</div>`;
  }

  // Buttons + questions
  const buttonsHtml = renderResponseButtons(spec_id, version, spec_title, bc_ticket_url);
  const questionsHtml = renderReviewQuestions(review_questions);

  // Full HTML body
  const bodyHtml = `<div style="font-family: Aptos, Arial, sans-serif; font-size: 14px; color: #2d3748; line-height: 1.55; max-width: 760px;">
  <div style="background: linear-gradient(135deg, #1a365d 0%, #2b6cb0 100%); color: #ffffff; padding: 18px 22px; border-radius: 8px; margin-bottom: 18px;">
    <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; opacity: 0.85;">Operator Build Kickoff &middot; Review</div>
    <div style="font-size: 20px; font-weight: 700; margin-top: 4px;">${spec_title}</div>
    <div style="font-size: 13px; opacity: 0.85; margin-top: 4px;">${spec_id} &middot; iteration ${version}</div>
  </div>

  <p>${summary_paragraph}</p>

  ${artifactHtml}
  ${questionsHtml}
  ${buttonsHtml}

  <div style="margin-top: 26px; padding-top: 14px; border-top: 1px solid #e2e8f0; font-size: 12px; color: #718096;">
    BC ticket: <a href="${bc_ticket_url}" style="color: #2b6cb0;">${bc_ticket_url}</a><br />
    Spec file: <a href="${spec_file_url}" style="color: #2b6cb0;">${spec_file_url}</a><br />
    Per operator-00-kickoff.md: silent for 48h = loop pauses; reply STOP at any time to halt this spec without approval.
  </div>
</div>
${HTML_SIGNATURE}`;

  const bodyText = `${spec_title} - ${spec_id} ${version}

${summary_paragraph}

To respond, reply to this email with one of these in the subject line:
  APPROVED      - ship it
  COMMENTS      - approve with notes (add notes in body)
  CHANGES       - request changes (describe what to change in body)
  STOP          - halt this spec's review loop

BC ticket: ${bc_ticket_url}
Spec file: ${spec_file_url}

${PLAIN_SIGNATURE}`;

  const transport = createTransport({
    host: 'smtp.mandrillapp.com',
    port: 587,
    auth: {
      user: process.env.MANDRILL_USERNAME || process.env.SMTP_USER,
      pass: process.env.MANDRILL_API_KEY || process.env.SMTP_PASS,
    },
  });

  try {
    const r = await transport.sendMail({
      from: '"Ali Muwwakkil" <ali@colaberry.com>',
      to: REVIEW_REPLY_ADDR,
      replyTo: REPLY_TO_ADDR,
      subject,
      text: bodyText,
      html: bodyHtml,
      attachments,
      headers: {
        'X-MC-Track': 'none',
        'X-MC-AutoText': 'false',
      },
    });
    console.log('Sent:', r.messageId);
    process.exit(0);
  } catch (e) {
    console.error('Failed:', e.message);
    process.exit(1);
  }
}

main();
