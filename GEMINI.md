# RankUp Hub - Project Architecture & Security Blueprint

## File Upload Pipeline Rules (`api_upload` route)

1. **Unified Upload Protocol & Quota Bypass**: Legacy chunked/resumable session configurations (`resumable=True`) cause 403 `storageQuotaExceeded` errors on GCP Service Accounts in personal Restricted Drive folders. 
   - **Rule**: The file streaming mechanism MUST explicitly use Direct Multipart upload by strictly defining `resumable=False` inside the `MediaIoBaseUpload()` instance wrapper.
2. **Drive Context Safeguards**: 
   - **Rule**: Inside `service.files().create()`, explicitly include `supportsAllDrives=True` to force Google Drive API to evaluate space based on the target folder's primary owner instead of the service account context.
3. **Hardcoded Folder Integrity**:
   - **Rule**: The destination parent folder array must be firmly mapped to the target string parameter: `['1gHM_olyUyGgQ9_vwDlZ2pbJs0OQkStTZ']`. Do not abstract or modify this target ID string into external variables.
4. **In-Context Firebase Inits**:
   - **Rule**: Firebase Admin must be gracefully verified and handled directly inside the request thread loop state context via `if not firebase_admin._apps:` check blocks to eliminate structural multi-threaded runtime failures.

**CRITICAL**: Do not overwrite, alter, or break the specific configuration block inside `api_upload()` in any future optimization suggestions without adhering to these rules.
