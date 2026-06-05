/**
 * Invitation email script for newly-provisioned advisor.colaberry.ai users.
 *
 * Sends an HTML email via Mandrill SMTP introducing the user to the platform,
 * listing what they've been provisioned with, and providing a Google SSO
 * sign-in CTA.
 *
 * Called by app/routers/admin.py user_new() after workspace + BC project
 * provisioning succeeds (or directly via this script for one-offs).
 *
 * USAGE (called from admin Python via subprocess OR run standalone):
 *   INVITE_PAYLOAD=/path/to/payload.json node sendOnboardingInviteEmail.js
 *
 * PAYLOAD SCHEMA:
 *   {
 *     "user_email": "karun@colaberry.com",
 *     "user_display_name": "Karun Swaroop",
 *     "tenant_id": "colaberry",
 *     "workspace_repo_url": "https://github.com/ColaberryIntern/karun-workspace",
 *     "personal_bc_project_url": "https://3.basecamp.com/3945211/projects/9999999",
 *     "tools_granted": ["gmail", "calendar", "basecamp", "github"]
 *   }
 *
 * Per the operator-00-kickoff spec § Phase B, this email is the user's first
 * touchpoint after admin provisioning completes.
 */

const fs = require('fs');
const { createTransport } = require('nodemailer');

const transport = createTransport({
  host: 'smtp.mandrillapp.com',
  port: 587,
  auth: {
    user: process.env.MANDRILL_USERNAME || process.env.SMTP_USER || 'ali@colaberry.com',
    pass: process.env.MANDRILL_API_KEY || process.env.SMTP_PASS,
  },
});

const HTML_SIGNATURE = `<table cellpadding="0" cellspacing="0" border="0" style="font-family: Aptos, Arial, sans-serif; font-size: 14px; color: #2d3748; border-left: 3px solid #1a365d; padding-left: 14px; margin-top: 20px;">
  <tr><td>
    <div style="font-weight: 700; font-size: 16px; color: #1a365d;">Ali Muwwakkil</div>
    <div style="color: #2b6cb0; font-weight: 600;">Managing Director / AI Systems Architect</div>
    <div style="color: #718096;">Colaberry Inc.</div>
    <div style="margin-top: 10px; color: #2d3748;">&#128205; 200 Chisholm Place, Suite 200 &middot; Plano, TX 75075</div>
    <div style="color: #2d3748;">&#9993; <a href="mailto:ali@colaberry.com" style="color: #2b6cb0; text-decoration: none;">ali@colaberry.com</a> &nbsp; &#127760; <a href="https://enterprise.colaberry.ai" style="color: #2b6cb0; text-decoration: none;">enterprise.colaberry.ai</a></div>
  </td></tr>
</table>`;

const PLAIN_SIGNATURE = `Ali Muwwakkil
Managing Director / AI Systems Architect
Colaberry Inc.

200 Chisholm Place, Suite 200, Plano, TX 75075
ali@colaberry.com  |  enterprise.colaberry.ai`;

const SIGN_IN_URL = 'https://advisor.colaberry.ai/auth/login';
const PORTAL_BASE = 'https://advisor.colaberry.ai';

function renderToolsList(tools) {
  if (!tools || tools.length === 0) return '<li>(none granted)</li>';
  const labels = {
    gmail: 'Gmail',
    calendar: 'Google Calendar',
    basecamp: 'Basecamp',
    ccpp: 'CCPP / Colaberry data',
    github: 'GitHub',
    mandrill: 'Mandrill (transactional email)',
    slack: 'Slack',
  };
  return tools.map(t => `<li>${labels[t] || t}</li>`).join('');
}

async function main() {
  const payloadPath = process.env.INVITE_PAYLOAD;
  if (!payloadPath) {
    console.error('INVITE_PAYLOAD env var required');
    process.exit(1);
  }
  const payload = JSON.parse(fs.readFileSync(payloadPath, 'utf8'));
  const {
    user_email,
    user_display_name,
    tenant_id,
    workspace_repo_url,
    personal_bc_project_url,
    tools_granted = [],
  } = payload;

  const subject = `Welcome to Colaberry advisor (${user_display_name})`;

  const bodyText = `Hi ${user_display_name},

Your Colaberry advisor.colaberry.ai workspace is ready.

WHAT YOU CAN DO

Sign in once with your Google account (${user_email}) and you will have access to:
${tools_granted.map(t => `  - ${t}`).join('\n') || '  (no tools granted yet — contact admin)'}

YOUR WORKSPACE LINKS

Sign in:                ${SIGN_IN_URL}
Portal:                 ${PORTAL_BASE}
Your GitHub workspace:  ${workspace_repo_url || '(not yet provisioned)'}
Your Basecamp project:  ${personal_bc_project_url || '(not yet provisioned)'}

WHAT TO DO FIRST

1. Click Sign in above. You will be redirected to Google to authenticate with ${user_email}.
2. Once signed in, visit the portal to see your provisioned tools.
3. Clone your GitHub workspace locally and run Claude Code in that directory. Your workspace comes pre-seeded with CLAUDE.md, PROGRESS.md, and OPERATOR_MEMORY.md.
4. Every Claude Code session you run creates a Basecamp ticket in your personal project so I can see what you are working on.

If you hit any issues, reply to this email.

${PLAIN_SIGNATURE}`;

  const bodyHtml = `<div style="font-family: Aptos, Arial, sans-serif; font-size: 14px; color: #2d3748; line-height: 1.55; max-width: 720px;">

<div style="background: linear-gradient(135deg, #1a365d 0%, #2b6cb0 100%); color: #ffffff; padding: 22px 26px; border-radius: 8px; margin-bottom: 22px;">
  <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; opacity: 0.85;">Welcome to Colaberry advisor</div>
  <div style="font-size: 22px; font-weight: 700; margin-top: 4px;">${user_display_name}, your workspace is ready</div>
  <div style="font-size: 13px; opacity: 0.9; margin-top: 6px;">Tenant: <code>${tenant_id || 'colaberry'}</code> &middot; Sign in with <code>${user_email}</code></div>
</div>

<p>Hi ${user_display_name},</p>

<p>Your Colaberry advisor.colaberry.ai workspace is ready. Sign in once with your Google account and everything below is yours.</p>

<div style="text-align: center; margin: 30px 0;">
  <a href="${SIGN_IN_URL}" style="display: inline-block; background: #2b6cb0; color: #ffffff; padding: 14px 36px; border-radius: 24px; text-decoration: none; font-weight: 700; font-size: 16px;">&#128274; Sign in with Google</a>
</div>

<h3 style="color: #1a365d; margin-top: 24px;">What you got provisioned with</h3>

<table style="border-collapse: collapse; width: 100%; font-size: 13px;">
  <thead><tr style="background: #1a365d; color: #ffffff;">
    <th style="text-align: left; padding: 9px 12px;">Resource</th>
    <th style="text-align: left; padding: 9px 12px;">Link</th>
  </tr></thead>
  <tbody>
    <tr style="background: #f7fafc;"><td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0;">Portal</td><td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0;"><a href="${PORTAL_BASE}" style="color: #2b6cb0;">advisor.colaberry.ai</a></td></tr>
    <tr><td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0;">Your GitHub workspace</td><td style="padding: 8px 12px; border-bottom: 1px solid #e2e8f0;">${workspace_repo_url ? `<a href="${workspace_repo_url}" style="color: #2b6cb0;">${workspace_repo_url}</a>` : '<em>(provisioning pending)</em>'}</td></tr>
    <tr style="background: #f7fafc;"><td style="padding: 8px 12px;">Your Basecamp project</td><td style="padding: 8px 12px;">${personal_bc_project_url ? `<a href="${personal_bc_project_url}" style="color: #2b6cb0;">Personal BC project</a>` : '<em>(provisioning pending)</em>'}</td></tr>
  </tbody>
</table>

<h3 style="color: #1a365d; margin-top: 24px;">Tools granted to you</h3>

<ul style="line-height: 1.7;">${renderToolsList(tools_granted)}</ul>

<h3 style="color: #1a365d; margin-top: 24px;">What to do first</h3>

<ol style="line-height: 1.7;">
  <li><strong>Click "Sign in with Google" above.</strong> Google will redirect back here once you authenticate with <code>${user_email}</code>.</li>
  <li><strong>Visit the portal</strong> to see your provisioned tools and dashboard.</li>
  <li><strong>Clone your GitHub workspace locally</strong> and run <code>claude</code> in that directory. Your workspace comes pre-seeded with CLAUDE.md, PROGRESS.md, OPERATOR_MEMORY.md, and the <code>.claude/</code> scaffolding that connects you to Colaberry's shared doctrine.</li>
  <li><strong>Every Claude Code session</strong> you run creates a Basecamp ticket in your personal project so progress is visible.</li>
</ol>

<p style="margin-top: 20px;">If you hit any issues, reply to this email.</p>

</div>
${HTML_SIGNATURE}`;

  try {
    const r = await transport.sendMail({
      from: '"Ali Muwwakkil" <ali@colaberry.com>',
      to: user_email,
      bcc: 'ali@colaberry.com',
      subject,
      text: bodyText,
      html: bodyHtml,
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
