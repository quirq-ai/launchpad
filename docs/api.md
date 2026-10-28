# Deployment endpoint map

Source: `xo-swarm-api`, `feat/launchpad-v2` at `fc15278`. Each user request sends the caller's Clerk key as `Authorization: Bearer XO_API_KEY`. No user ID is accepted by the CLI.

| Method | Path | Authentication | Command |
|---|---|---|---|
| GET | `/launchpad/templates` | `XO_API_KEY` | `templates` |
| GET | `/launchpad/apps` | `XO_API_KEY` | `app list` |
| POST | `/launchpad/apps` | `XO_API_KEY` | `app create NAME` |
| GET | `/launchpad/apps/{app_id}` | `XO_API_KEY` | `app get APP` |
| DELETE | `/launchpad/apps/{app_id}` | `XO_API_KEY` | `app delete APP` |
| GET | `/launchpad/apps/{app_id}/status` | `XO_API_KEY` | `app status APP` |
| GET | `/launchpad/apps/{app_id}/logs` | `XO_API_KEY` | `app logs APP` |
| GET | `/launchpad/apps/{app_id}/events` | `XO_API_KEY` | `app events APP` |
| GET | `/launchpad/apps/{app_id}/artifacts` | `XO_API_KEY` | `app artifacts APP` |
| GET | `/launchpad/apps/{app_id}/releases` | `XO_API_KEY` | `app releases APP` |
| POST | `/launchpad/apps/{app_id}/deploy` | `XO_API_KEY` | `app deploy APP` |
| POST | `/launchpad/apps/{app_id}/rollback` | `XO_API_KEY` | `app rollback APP RELEASE_ID` |
| POST | `/launchpad/apps/{app_id}/start` | `XO_API_KEY` | `app start APP` |
| POST | `/launchpad/apps/{app_id}/stop` | `XO_API_KEY` | `app stop APP` |
| POST | `/launchpad/apps/{app_id}/restart` | `XO_API_KEY` | `app restart APP` |
| GET | `/launchpad/apps/{app_id}/domains` | `XO_API_KEY` | `app domains list APP` |
| POST | `/launchpad/apps/{app_id}/domains` | `XO_API_KEY` | `app domains add APP HOST` |
| POST | `/launchpad/apps/{app_id}/domains/{domain_id}/verify` | `XO_API_KEY` | `app domains verify APP DOMAIN_ID` |
| DELETE | `/launchpad/apps/{app_id}/domains/{domain_id}` | `XO_API_KEY` | `app domains remove APP DOMAIN_ID` |
| POST | `/launchpad/apps/{app_id}/robots/push/rotate` | `XO_API_KEY` | `app credentials rotate APP` |
| GET | `/launchpad/apps/{app_id}/env` | `XO_API_KEY` | `app env list APP` |
| PUT | `/launchpad/apps/{app_id}/env` | `XO_API_KEY` | `app env replace APP` |
| PUT | `/launchpad/apps/{app_id}/storage` | `XO_API_KEY` | `app storage resize APP GB` |
| PUT | `/launchpad/apps/{app_id}/port` | `XO_API_KEY` | `app port set APP PORT` |
| PUT | `/launchpad/apps/{app_id}/health` | `XO_API_KEY` | `app health set APP --path PATH` |
| POST | `/harbor-registry-config` | `XO_API_KEY` | `api POST /harbor-registry-config` |
| POST | `/get-deployment-details-by-user-id` | `XO_API_KEY` | `api POST /get-deployment-details-by-user-id` |
| POST | `/deploy-to-xo` | `XO_API_KEY` | `api POST /deploy-to-xo` |
| POST | `/get-xo-app-logs` | `XO_API_KEY` | `api POST /get-xo-app-logs` |
| POST | `/delete-xo-app` | `XO_API_KEY` | `api POST /delete-xo-app` |
| POST | `/update-target-port` | `XO_API_KEY` | `api POST /update-target-port` |
| POST | `/start-xo-app` | `XO_API_KEY` | `api POST /start-xo-app` |
| POST | `/stop-xo-app` | `XO_API_KEY` | `api POST /stop-xo-app` |
| POST | `/redeploy-xo-app` | `XO_API_KEY` | `api POST /redeploy-xo-app` |
| POST | `/github-main-flow` | `XO_API_KEY` | `api POST /github-main-flow` |
| POST | `/deploy-n8n` | `XO_API_KEY` | `api POST /deploy-n8n` |
| POST | `/deploy-gaia` | `XO_API_KEY` | `api POST /deploy-gaia` |
| POST | `/launchpad/webhooks/harbor` | `LAUNCHPAD_WEBHOOK_SECRET` | `Service callback; not invoked by CLI` |

The canonical commands cover every user route on the v2 router. Template administration is not included in this CLI. Legacy routes duplicate the same deployment operations and are available through the authenticated raw `api` command. Harbor webhooks are documented for completeness but are not called by the CLI.
