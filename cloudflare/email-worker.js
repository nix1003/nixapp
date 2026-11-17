// Cloudflare Email Worker for nixapp.org
// Receives every email sent to any address @nixapp.org (catch-all rule)
// and forwards the raw message to the site, which stores it for the Inbox page.
//
// Required secret on this Worker: INBOUND_SECRET (same value as INBOUND_EMAIL_SECRET on Render)

export default {
  async email(message, env, ctx) {
    const raw = await new Response(message.raw).arrayBuffer();

    const res = await fetch("https://nixapp.org/api/inbound-email", {
      method: "POST",
      headers: {
        "Content-Type": "message/rfc822",
        "X-Inbound-Secret": env.INBOUND_SECRET,
        "X-Envelope-To": message.to,
        "X-Envelope-From": message.from,
      },
      body: raw,
    });

    if (!res.ok) {
      // Throwing makes Cloudflare report the delivery as failed so it shows in the Worker logs
      throw new Error(`nixapp.org rejected email: ${res.status} ${await res.text()}`);
    }
  },
};
