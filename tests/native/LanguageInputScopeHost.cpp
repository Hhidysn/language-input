#include <InputScope.h>
#include <msctf.h>
#include <windows.h>

namespace {

constexpr wchar_t kWindowClass[] = L"LanguageInputScopeHost";
constexpr CLSID kWeaselTextService = {
    0xa3f4cded,
    0xb1e9,
    0x41ee,
    {0x9c, 0xa6, 0x7b, 0x4d, 0x0d, 0xe6, 0xcb, 0x0a}};
constexpr GUID kWeaselProfile = {
    0x3d02cab6,
    0x2b8e,
    0x4781,
    {0xba, 0x20, 0x1c, 0x92, 0x67, 0x52, 0x94, 0x67}};

HRESULT ActivateWeaselForProcess() {
  ITfInputProcessorProfileMgr* profiles = nullptr;
  HRESULT result = CoCreateInstance(
      CLSID_TF_InputProcessorProfiles, nullptr, CLSCTX_INPROC_SERVER,
      IID_ITfInputProcessorProfileMgr,
      reinterpret_cast<void**>(&profiles));
  if (FAILED(result) || !profiles)
    return result;
  result = profiles->ActivateProfile(
      TF_PROFILETYPE_INPUTPROCESSOR,
      MAKELANGID(LANG_CHINESE, SUBLANG_CHINESE_SIMPLIFIED),
      kWeaselTextService, kWeaselProfile, nullptr,
      TF_IPPMF_FORPROCESS | TF_IPPMF_DONTCARECURRENTINPUTLANGUAGE);
  profiles->Release();
  return result;
}

void ApplyInputScope(HWND window, InputScope scope) {
  HMODULE module = LoadLibraryW(L"msctf.dll");
  if (!module)
    return;
  using SetInputScopeFunction = HRESULT(WINAPI*)(HWND, InputScope);
  auto set_input_scope = reinterpret_cast<SetInputScopeFunction>(
      GetProcAddress(module, "SetInputScope"));
  if (set_input_scope)
    set_input_scope(window, scope);
  FreeLibrary(module);
}

void SetControlFont(HWND control) {
  SendMessageW(control, WM_SETFONT,
               reinterpret_cast<WPARAM>(GetStockObject(DEFAULT_GUI_FONT)),
               TRUE);
}

HWND AddLabel(HWND parent, const wchar_t* text, int y) {
  HWND label = CreateWindowExW(0, L"STATIC", text, WS_CHILD | WS_VISIBLE,
                               20, y, 620, 22, parent, nullptr,
                               GetModuleHandleW(nullptr), nullptr);
  SetControlFont(label);
  return label;
}

HWND AddEdit(HWND parent, int id, int y, DWORD extra_style) {
  HWND edit = CreateWindowExW(
      WS_EX_CLIENTEDGE, L"EDIT", L"",
      WS_CHILD | WS_VISIBLE | WS_TABSTOP | ES_AUTOHSCROLL | extra_style, 20,
      y, 620, 34, parent, reinterpret_cast<HMENU>(static_cast<INT_PTR>(id)),
      GetModuleHandleW(nullptr), nullptr);
  SetControlFont(edit);
  return edit;
}

LRESULT CALLBACK WindowProc(HWND window,
                            UINT message,
                            WPARAM wparam,
                            LPARAM lparam) {
  switch (message) {
    case WM_CREATE: {
      AddLabel(window, L"A. Plain Win32 Edit (no explicit InputScope)", 18);
      HWND plain = AddEdit(window, 101, 43, 0);

      AddLabel(window, L"B. Explicit IS_DEFAULT InputScope", 94);
      HWND default_scope = AddEdit(window, 102, 119, 0);
      ApplyInputScope(default_scope, IS_DEFAULT);

      AddLabel(window, L"C. Password: ES_PASSWORD + IS_PASSWORD", 170);
      HWND password = AddEdit(window, 103, 195, ES_PASSWORD);
      ApplyInputScope(password, IS_PASSWORD);

      SetFocus(plain);
      return 0;
    }
    case WM_DESTROY:
      PostQuitMessage(0);
      return 0;
    default:
      return DefWindowProcW(window, message, wparam, lparam);
  }
}

}  // namespace

int WINAPI wWinMain(HINSTANCE instance,
                    HINSTANCE,
                    wchar_t*,
                    int show_command) {
  CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);

  WNDCLASSEXW window_class = {};
  window_class.cbSize = sizeof(window_class);
  window_class.hInstance = instance;
  window_class.lpfnWndProc = WindowProc;
  window_class.lpszClassName = kWindowClass;
  window_class.hCursor = LoadCursorW(nullptr, IDC_IBEAM);
  window_class.hbrBackground =
      reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
  if (!RegisterClassExW(&window_class)) {
    CoUninitialize();
    return 1;
  }

  HWND window = CreateWindowExW(
      0, kWindowClass, L"Language Input Scope Test",
      WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX,
      CW_USEDEFAULT, CW_USEDEFAULT, 680, 300, nullptr, nullptr, instance,
      nullptr);
  if (!window) {
    CoUninitialize();
    return 2;
  }

  ShowWindow(window, show_command);
  UpdateWindow(window);
  HRESULT activation_result = ActivateWeaselForProcess();
  wchar_t title[128] = {};
  wsprintfW(title, L"Language Input Scope Test (activate=0x%08lX)",
            static_cast<unsigned long>(activation_result));
  SetWindowTextW(window, title);

  MSG message = {};
  while (GetMessageW(&message, nullptr, 0, 0) > 0) {
    TranslateMessage(&message);
    DispatchMessageW(&message);
  }

  CoUninitialize();
  return static_cast<int>(message.wParam);
}
