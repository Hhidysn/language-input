#pragma once

#include <InputScope.h>
#include <msctf.h>

bool IsSensitiveInputScope(InputScope input_scope);

// Returns true when the application exposed a readable input-scope property.
// The sensitive result is written only when the query succeeds.
bool QuerySensitiveInputScope(ITfContext* context,
                              TfClientId client_id,
                              bool* sensitive);
