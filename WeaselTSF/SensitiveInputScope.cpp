#include "stdafx.h"

#include "SensitiveInputScope.h"

#include <OleAuto.h>
#include <SensitiveInput.h>
#include "WeaselTSF.h"

namespace {

constexpr GUID kInputScopeProperty = {
    0x1713dd5a,
    0x68e7,
    0x4a5b,
    {0x9a, 0xf6, 0x59, 0x2a, 0x59, 0x5c, 0x77, 0x8d}};

class SensitiveInputScopeEditSession : public ITfEditSession {
 public:
  explicit SensitiveInputScopeEditSession(ITfContext* context)
      : context_(context) {}

  STDMETHODIMP QueryInterface(REFIID iid, void** object) override {
    if (!object)
      return E_INVALIDARG;
    *object = nullptr;
    if (iid == IID_IUnknown || iid == IID_ITfEditSession) {
      *object = static_cast<ITfEditSession*>(this);
      AddRef();
      return S_OK;
    }
    return E_NOINTERFACE;
  }

  STDMETHODIMP_(ULONG) AddRef() override {
    return static_cast<ULONG>(InterlockedIncrement(&references_));
  }

  STDMETHODIMP_(ULONG) Release() override {
    ULONG references = static_cast<ULONG>(InterlockedDecrement(&references_));
    if (references == 0)
      delete this;
    return references;
  }

  STDMETHODIMP DoEditSession(TfEditCookie edit_cookie) override {
    com_ptr<ITfReadOnlyProperty> property;
    HRESULT result = context_->GetAppProperty(kInputScopeProperty, &property);
    if (FAILED(result) || !property)
      return result;

    TF_SELECTION selection = {};
    ULONG fetched = 0;
    result = context_->GetSelection(edit_cookie, TF_DEFAULT_SELECTION, 1,
                                    &selection, &fetched);
    if (FAILED(result) || fetched == 0 || !selection.range)
      return FAILED(result) ? result : S_FALSE;

    VARIANT value;
    VariantInit(&value);
    result = property->GetValue(edit_cookie, selection.range, &value);
    if (FAILED(result)) {
      selection.range->Release();
      VariantClear(&value);
      return result;
    }
    selection.range->Release();

    if (value.vt == VT_EMPTY || value.vt == VT_NULL) {
      // A successfully read empty property is the ordinary/default scope.
      // Distinguish that from an edit-session failure so callers can fail
      // closed only when the scope genuinely could not be inspected.
      found_ = true;
    } else if (value.vt == VT_UNKNOWN && value.punkVal) {
      com_ptr<ITfInputScope> input_scope;
      ITfInputScope* raw_input_scope = nullptr;
      HRESULT interface_result = value.punkVal->QueryInterface(
          IID_ITfInputScope, reinterpret_cast<void**>(&raw_input_scope));
      if (SUCCEEDED(interface_result) && raw_input_scope) {
        input_scope.Attach(raw_input_scope);
        InputScope* scopes = nullptr;
        UINT count = 0;
        HRESULT scopes_result = input_scope->GetInputScopes(&scopes, &count);
        if (SUCCEEDED(scopes_result)) {
          found_ = true;
          for (UINT index = 0; index < count; ++index)
            sensitive_ = sensitive_ || IsSensitiveInputScope(scopes[index]);
        }
        CoTaskMemFree(scopes);
      }
    } else if (value.vt == VT_I4) {
      found_ = true;
      sensitive_ = IsSensitiveInputScope(static_cast<InputScope>(value.lVal));
    }
    VariantClear(&value);
    return S_OK;
  }

  bool found() const { return found_; }
  bool sensitive() const { return sensitive_; }

 private:
  ~SensitiveInputScopeEditSession() = default;

  LONG references_ = 1;
  com_ptr<ITfContext> context_;
  bool found_ = false;
  bool sensitive_ = false;
};

}  // namespace

bool IsSensitiveInputScope(InputScope input_scope) {
  switch (input_scope) {
    case IS_PASSWORD:
    case IS_PRIVATE:
    case IS_NUMERIC_PASSWORD:
    case IS_NUMERIC_PIN:
    case IS_ALPHANUMERIC_PIN:
    case IS_ALPHANUMERIC_PIN_SET:
      return true;
    default:
      return false;
  }
}

bool QuerySensitiveInputScope(ITfContext* context,
                              TfClientId client_id,
                              bool* sensitive) {
  if (!context || !sensitive || client_id == TF_CLIENTID_NULL)
    return false;

  auto* edit_session = new SensitiveInputScopeEditSession(context);
  HRESULT session_result = E_FAIL;
  HRESULT request_result = context->RequestEditSession(
      client_id, edit_session, TF_ES_SYNC | TF_ES_READ, &session_result);
  bool found = SUCCEEDED(request_result) && SUCCEEDED(session_result) &&
               edit_session->found();
  if (found)
    *sensitive = edit_session->sensitive();
  edit_session->Release();
  return found;
}

com_ptr<ITfContext> WeaselTSF::_GetFocusedContext() {
  com_ptr<ITfDocumentMgr> document_manager;
  com_ptr<ITfContext> context;
  if (_pThreadMgr && SUCCEEDED(_pThreadMgr->GetFocus(&document_manager)) &&
      document_manager) {
    document_manager->GetTop(&context);
  }
  return context;
}

void WeaselTSF::_UpdateClientCapabilities(com_ptr<ITfContext> context,
                                          bool force) {
  if (!context)
    context = _GetFocusedContext();

  const bool context_changed = context != _sensitive_context;
  if (context_changed) {
    _sensitive_context = context;
    _input_scope_known = false;
    _input_scope_sensitive = true;
  }

  // A browser or rich document can reuse one TSF context for ordinary and
  // password ranges. Re-read the property for every key instead of caching
  // only by context identity. A failed inspection is sensitive by default.
  bool scope_sensitive = false;
  bool scope_known =
      context && QuerySensitiveInputScope(context, _tfClientId,
                                          &scope_sensitive);
  if (scope_known) {
    _input_scope_known = true;
    _input_scope_sensitive = scope_sensitive;
  } else {
    _input_scope_known = false;
    _input_scope_sensitive = true;
  }

  HWND window = nullptr;
  if (context) {
    com_ptr<ITfContextView> view;
    if (SUCCEEDED(context->GetActiveView(&view)) && view)
      view->GetWnd(&window);
  }
  if (!window)
    window = _GetFocusedContextWindow();

  const auto conventional_state =
      weasel::QueryConventionalInputState(window);
  bool sensitive = weasel::ShouldTreatInputAsSensitive(
      _input_scope_known, _input_scope_sensitive, conventional_state);
  DWORD client_caps =
      sensitive ? weasel::CLIENT_CAP_SENSITIVE : weasel::CLIENT_CAP_NONE;
  if (force || client_caps != _client_caps) {
    m_client.FocusIn(client_caps);
    _client_caps = client_caps;
  }
}
