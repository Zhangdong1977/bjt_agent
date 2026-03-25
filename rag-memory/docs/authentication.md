# Authentication Guide

This guide covers all authentication methods supported by our API.

## API Key Authentication

The simplest way to authenticate is using an API key.

### Getting an API Key

1. Log into your dashboard
2. Navigate to Settings > API Keys
3. Click "Generate New Key"
4. Copy the key - you won't see it again!

### Using the API Key

Include the key in the `Authorization` header:

```
Authorization: Bearer YOUR_API_KEY
```

Example:
```bash
curl -H "Authorization: Bearer YOUR_API_KEY" \
  https://api.example.com/v1/users
```

## OAuth 2.0 Authentication

For third-party integrations, we support OAuth 2.0.

### Registration

1. Create an application in the developer portal
2. Set your redirect URIs
3. Note your client ID and secret

### OAuth Flow

1. Redirect users to: `https://example.com/oauth/authorize`
2. User approves your app
3. User is redirected to your redirect URI with a code
4. Exchange the code for an access token

### Token Endpoint

```http
POST /oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code&code=YOUR_CODE&redirect_uri=YOUR_REDIRECT_URI
```

### Refresh Tokens

Access tokens expire after 1 hour. Use the refresh token to get a new one:

```http
POST /oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token&refresh_token=YOUR_REFRESH_TOKEN
```

## JWT Authentication

JSON Web Tokens are used for session management.

### Structure

Our JWTs contain:
- `sub`: User ID
- `exp`: Expiration time
- `iat`: Issued at time
- `scopes`: Available permissions

### Validation

Always verify:
1. Signature using your secret key
2. Expiration time
3. Issuer claim

## Security Best Practices

1. Never expose API keys in client-side code
2. Use HTTPS for all authenticated requests
3. Implement rate limiting
4. Rotate keys regularly
5. Use short-lived tokens with refresh tokens
6. Log all authentication attempts
7. Implement IP whitelisting for sensitive operations

## Troubleshooting

### Common Errors

**401 Unauthorized**
- Check your API key is valid
- Verify the key hasn't been revoked
- Ensure you're using the correct environment (sandbox vs production)

**403 Forbidden**
- Your key doesn't have permission for this resource
- Check your account is in good standing
- Verify you're not exceeding rate limits

**429 Too Many Requests**
- Implement exponential backoff
- Consider upgrading your plan
- Cache responses when possible

### Testing Authentication

Test your credentials:

```bash
curl -v https://api.example.com/v1/auth/test \
  -H "Authorization: Bearer YOUR_API_KEY"
```

This endpoint returns your user details if authentication succeeds.
