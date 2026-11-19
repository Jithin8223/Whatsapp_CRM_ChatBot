# Website chat

1. Sign in as a workspace administrator and open **Website chatbot**.
2. Set the title, welcome text, and accent color. Enter each allowed website origin, such as `https://support.example`, without a path. Development mode also accepts HTTP loopback origins.
3. Publish the widget and copy the displayed snippet into a page on an allowed origin. Serve the built frontend’s `widget.js` and forward `/api/` and `/ws/` to Django and Daphne on the same platform origin. Use HTTPS in production.
4. Open **Products and leads** to add products. Enter verified price, currency, availability, features, specifications, warranty and additional facts. Use an HTTPS image URL for a product image. Add product FAQs and upload UTF-8 `.txt` or `.md` notes up to 20 KB. Uploads are parsed as text and stored with the product; other formats are rejected.
5. Add approved business answers under **Business knowledge**, configure the AI provider and explicitly enable AI processing. Product questions retrieve facts only from the named product. If a product is ambiguous the assistant asks which one; missing facts are reported instead of guessed. Comparisons include only products named by the customer.
6. In **Conversations**, select the **Website** filter. Choose **AI automatic**, **AI draft · approval required**, or **Human** per conversation. Drafts stay out of the outbound queue until an authorized workspace member approves them. Human replies switch the thread to Human mode; switching away from AI cancels unapproved drafts.

Guests can select **Request a human** in the widget. The thread appears under **Pending** with **Waiting for Agent** status. **Take over** assigns the thread to the agent and pauses automated replies. Use **Return to AI** to release the assignment and resume automation. **Resolve** closes the conversation.

Widget conversations are visible only in the workspace that owns the widget. Select that workspace in the CRM, then choose **Conversations → Website**. Guest labels such as `Guest #0001` are unique within that workspace and remain stable when the guest resumes the same session. A visitor can add a display name voluntarily.

After starting a session, a guest may optionally share a display name, email address, and/or international mobile number. They can remove shared email and mobile details from the same form. Email addresses and mobile numbers are encrypted at rest and visible only to authenticated staff in the owning workspace. Internal notes are available in **Customer details** and stay out of guest chat history.

Edit suggested questions in the widget settings. Selecting one sends it as a regular guest message. **Save changes** keeps the widget's current publication status. Use **Publish widget** to make an unpublished widget available on its listed origins.

The knowledge editor supports search and categories. **Add starter drafts** inserts generic questions with blank answers. Drafts stay inactive until you enter approved answers and activate them. Product FAQs and documentation are retrieved only for relevant products; general business answers remain workspace-scoped.

Guest sessions require no login, phone number, or name. The browser retains an opaque credential in tab session storage for up to 24 hours. The server stores a digest tied to the widget, origin, and conversation. The widget loads the latest 100 messages. Staff can access older messages through the paginated inbox.

## External model

Set `CRM_AI_API_KEY` and `CRM_AI_MODEL` in the server environment and recreate the backend and worker to load them. Grant access to the configured model before selecting OpenAI for a workspace. The widget indicates when chat text may be processed externally. The server sends only bounded conversation excerpts and retrieved workspace knowledge to the Responses API with response storage disabled in the request. Keep secrets out of frontend configuration and source control.

The local knowledge provider returns configured business facts and approved answers. OpenAI responses are used only when a workspace administrator enables processing and selects OpenAI, and the server has its API key and model configured. Ticket and appointment requests hand off to staff because this deployment has no authorized ticketing or scheduling connector.

## Optional WhatsApp

Set a public business WhatsApp number in international digit format and optionally add a prefilled message. The widget will show a `wa.me` link. Leave the number empty to hide it.

The WPPConnect integration is disabled unless the deployment sets `WPPCONNECT_ENABLED=true`. Website chat runs independently. The widget’s WhatsApp contact link can open WhatsApp even when CRM messaging is disconnected.

The website chatbot screens are shown by default. Set `VITE_SHOW_WEBSITE_CHAT_UI=false` only if the workspace dashboard should hide website-chat administration; the embeddable widget remains available at `/widget.js`. The local test page uses `/whatsapp-link.js`; install the website widget snippet to add customer chat to a site.

For the WhatsApp-only button, open **WhatsApp AI**, save a public business number, optional greeting, and exact allowed site origins, then install the generated `whatsapp-link.js` snippet. The snippet does not start a guest chat or collect visitor details. It displays no launcher when the business number is absent.

To expose pairing in the local deployment, set `WPPCONNECT_ENABLED=true` and recreate the backend and worker. Then have an authorized administrator review **WhatsApp integration** and pair or reconnect the approved business account. A QR code is used by the administrator during device linking. Customers use their ordinary WhatsApp chat.

In a development deployment with `DJANGO_DEBUG=true`, workspace administrators can open **Test WhatsApp AI**, enter a dummy number, and send a simulated inbound message. Open the linked conversation in a second tab to take over, reply, and return it to AI. Replies remain in the CRM and carry a **Simulation** label. Enable workspace automation and add approved FAQ answers before testing AI. A simulated recipient can never be dispatched through WPPConnect.

In **WhatsApp AI**, customize the greeting and numbered choices. Each choice can point to an approved FAQ question or hand off to an agent. A greeting at the start of a new conversation shows the menu. The reply worker accepts a number after showing the menu and continues using the workspace's approved knowledge for normal questions.

Only an authorized administrator should initialize or pair a business WhatsApp account from the optional integration menu. Customers use ordinary WhatsApp messaging. WPPConnect is unofficial and may carry WhatsApp account restriction risks.

To enable automated WhatsApp replies, first connect the authorized business session, enable automatic replies for the workspace, then explicitly select **Enable AI replies** on that connection. New incoming threads can start in AI mode. Existing threads retain their mode until an agent uses **Return to AI**. **Take over** stops pending AI jobs and cancels automatic replies still in the outbound queue. A send already submitted to the provider cannot be recalled.

Existing provider records, credentials, and sessions are kept. Simulated adapter tests do not establish live WhatsApp delivery. Verify both inbound and outbound delivery with an authorized business session before relying on it.

## Retention and data requests

- Workspace administrators can export or explicitly delete an individual website guest conversation from the inbox. Deletion revokes the guest session.
- Retention is optional and applies only to closed website conversations. With a policy saved, an authorized operator can preview it with `docker compose exec -T backend python manage.py guest_retention WORKSPACE_UUID`. Add `--apply` only after reviewing the preview and obtaining authorization for deletion. The application does not schedule that command automatically.
- For legacy WhatsApp data, verify the requester’s authority, identify the specific workspace, contact, conversations, and provider session, and review applicable retention requirements. Export only the approved data using a restricted administrative process. Have an authorized operator review the exact deletion scope, including backups and provider-side copies, before running a targeted removal. The integration’s **Remove connection** action logs out the linked account and preserves conversation history. Guest retention and guest deletion do not process legacy WhatsApp conversations.
