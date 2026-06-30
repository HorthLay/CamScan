# Laravel to FastAPI Sync Guide

To sync user edits from CamScanWeb (Laravel) to CamScan FastAPI, you need to configure Laravel to call the FastAPI endpoint when a user is updated.

## FastAPI Endpoint

FastAPI has a sync endpoint ready to receive updates:
- **URL**: `POST http://your-fastapi-url/register/sync-from-laravel`
- **Method**: POST
- **Content-Type**: application/x-www-form-urlencoded (form data)

## Required Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| user_id | int | Yes | The user ID to update |
| name | string | No | User's name |
| date_of_birth | string | No | Date of birth in ISO format (YYYY-MM-DD) |
| age | int | No | User's age |
| note | string | No | User note (walkout, work, resign) |
| ai_notes | string | No | AI-generated notes |

## Laravel Implementation

### Option 1: Using Model Events (Recommended)

Add this to your User model in Laravel:

```php
<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class User extends Model
{
    protected static function booted()
    {
        static::updated(function ($user) {
            // Sync to FastAPI when user is updated
            $fastapiUrl = env('FASTAPI_URL', 'http://127.0.0.1:8001');
            
            try {
                $response = \Http::post("$fastapiUrl/register/sync-from-laravel", [
                    'user_id' => $user->id,
                    'name' => $user->name,
                    'date_of_birth' => $user->date_of_birth,
                    'age' => $user->age,
                    'note' => $user->note,
                    'ai_notes' => $user->ai_notes,
                ]);
                
                // Log success or error
                if ($response->successful()) {
                    \Log::info("Synced user {$user->id} to FastAPI");
                } else {
                    \Log::error("Failed to sync user {$user->id} to FastAPI: " . $response->body());
                }
            } catch (\Exception $e) {
                \Log::error("Exception syncing user {$user->id} to FastAPI: " . $e->getMessage());
            }
        });
    }
}
```

### Option 2: Using Controller

In your UserController, after updating a user:

```php
<?php

namespace App\Http\Controllers;

use App\Models\User;
use Illuminate\Http\Request;

class UserController extends Controller
{
    public function update(Request $request, User $user)
    {
        // Update user fields from request
        $user->update($request->all());
        
        // Sync to FastAPI
        $fastapiUrl = env('FASTAPI_URL', 'http://127.0.0.1:8001');
        
        try {
            $response = \Http::post("$fastapiUrl/register/sync-from-laravel", [
                'user_id' => $user->id,
                'name' => $user->name,
                'date_of_birth' => $user->date_of_birth,
                'age' => $user->age,
                'note' => $user->note,
                'ai_notes' => $user->ai_notes,
            ]);
            
            if (!$response->successful()) {
                \Log::error("FastAPI sync failed: " . $response->body());
            }
        } catch (\Exception $e) {
            \Log::error("FastAPI sync exception: " . $e->getMessage());
        }
        
        return response()->json($user);
    }
}
```

### Option 3: Using HTTP Client Directly

```php
$client = new \GuzzleHttp\Client();
$response = $client->post('http://127.0.0.1:8001/register/sync-from-laravel', [
    'form_params' => [
        'user_id' => 1,
        'name' => 'John Doe',
        'note' => 'work',
        'ai_notes' => 'Custom notes',
    ]
]);
```

## Laravel Configuration

Add to your `.env` file:

```env
FASTAPI_URL=http://127.0.0.1:8001
```

## Testing

You can test the FastAPI endpoint directly using curl:

```bash
curl -X POST http://127.0.0.1:8001/register/sync-from-laravel \
  -d "user_id=1" \
  -d "name=John Doe" \
  -d "note=work" \
  -d "ai_notes=Test notes"
```

Check the FastAPI console for debug output to verify it received the request.

## Debugging

If it's still not working:

1. Check FastAPI console logs for the debug messages
2. Verify Laravel is calling the correct URL
3. Check network connectivity between Laravel and FastAPI
4. Verify CORS settings if calling from browser
5. Check firewall settings

## FastAPI CORS

Make sure FastAPI accepts requests from Laravel. In `main.py`, CORS is already configured:

```python
cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "*").split(",")
    if origin.strip()
]
```

Set `CORS_ORIGINS` in your FastAPI `.env` if needed:

```env
CORS_ORIGINS=http://127.0.0.1:8000,http://localhost:8000
```
