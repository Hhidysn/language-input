#include "stdafx.h"
#include "WeaselServerApp.h"
#include <exdisp.h>
#include <shldisp.h>
#include <shlobj.h>
#include <filesystem>

bool WeaselServerApp::open_settings() {
  const auto dir = install_dir();
  const auto settings = dir / L"settings" / L"LanguageInputSettings.exe";
  std::error_code error;
  if (!std::filesystem::is_regular_file(settings, error))
    return execute(dir / L"WeaselDeployer.exe", std::wstring());

  // The installer is elevated, possibly as a different user. Ask the desktop
  // shell to open settings in the logged-in user's session and profile.
  // https://devblogs.microsoft.com/oldnewthing/20131118-00/?p=2643
  CComPtr<IShellWindows> windows;
  if (FAILED(windows.CoCreateInstance(CLSID_ShellWindows)))
    return false;
  CComVariant location(CSIDL_DESKTOP);
  CComVariant root;
  long hwnd = 0;
  CComPtr<IDispatch> desktop;
  if (FAILED(windows->FindWindowSW(&location, &root, SWC_DESKTOP, &hwnd,
                                 SWFO_NEEDDISPATCH, &desktop)) ||
      !desktop)
    return false;
  CComQIPtr<IServiceProvider> provider(desktop);
  CComPtr<IShellBrowser> browser;
  if (!provider || FAILED(provider->QueryService(SID_STopLevelBrowser,
                                                IID_PPV_ARGS(&browser))) ||
      !browser)
    return false;
  CComPtr<IShellView> view;
  if (FAILED(browser->QueryActiveShellView(&view)) || !view)
    return false;
  CComPtr<IDispatch> background;
  if (FAILED(view->GetItemObject(SVGIO_BACKGROUND,
                                IID_PPV_ARGS(&background))))
    return false;
  CComQIPtr<IShellFolderViewDual> folder(background);
  CComPtr<IDispatch> application;
  if (!folder || FAILED(folder->get_Application(&application)))
    return false;
  CComQIPtr<IShellDispatch2> shell(application);
  return shell && SUCCEEDED(shell->ShellExecute(
                      CComBSTR(settings.c_str()), CComVariant(L"--show"),
                      CComVariant(dir.c_str()), CComVariant(L"open"),
                      CComVariant(SW_SHOWNORMAL)));
}

WeaselServerApp::WeaselServerApp()
    : m_handler(std::make_unique<RimeWithWeaselHandler>(&m_ui)),
      tray_icon(m_ui) {
  // m_handler.reset(new RimeWithWeaselHandler(&m_ui));
  m_server.SetRequestHandler(m_handler.get());
  SetupMenuHandlers();
}

WeaselServerApp::~WeaselServerApp() {}

int WeaselServerApp::Run() {
  if (!m_server.Start())
    return -1;

  // win_sparkle_set_appcast_url("http://localhost:8000/weasel/update/appcast.xml");
  win_sparkle_set_registry_path("Software\\Rime\\Weasel\\Updates");
  if (GetThreadUILanguage() ==
      MAKELANGID(LANG_CHINESE, SUBLANG_CHINESE_TRADITIONAL))
    win_sparkle_set_lang("zh-TW");
  else if (GetThreadUILanguage() ==
           MAKELANGID(LANG_CHINESE, SUBLANG_CHINESE_SIMPLIFIED))
    win_sparkle_set_lang("zh-CN");
  else
    win_sparkle_set_lang("en");
  win_sparkle_init();
  m_ui.Create(m_server.GetHWnd());

  m_handler->Initialize();
  m_handler->SetAsyncRefreshWindow(m_server.GetHWnd());
  m_handler->OnUpdateUI([this]() { tray_icon.Refresh(); });

  tray_icon.Create(m_server.GetHWnd());
  tray_icon.Refresh();

  int ret = m_server.Run();

  m_handler->Finalize();
  m_ui.Destroy();
  tray_icon.RemoveIcon();
  win_sparkle_cleanup();

  return ret;
}

void WeaselServerApp::SetupMenuHandlers() {
  std::filesystem::path dir = install_dir();
  m_server.AddMenuHandler(ID_WEASELTRAY_QUIT,
                          [this] { return m_server.Stop() == 0; });
  m_server.AddMenuHandler(ID_WEASELTRAY_DEPLOY,
                          std::bind(execute, dir / L"WeaselDeployer.exe",
                                    std::wstring(L"/deploy")));
  m_server.AddMenuHandler(
      ID_WEASELTRAY_SETTINGS,
      open_settings);
  m_server.AddMenuHandler(
      ID_WEASELTRAY_DICT_MANAGEMENT,
      std::bind(execute, dir / L"WeaselDeployer.exe", std::wstring(L"/dict")));
  m_server.AddMenuHandler(
      ID_WEASELTRAY_SYNC,
      std::bind(execute, dir / L"WeaselDeployer.exe", std::wstring(L"/sync")));
  m_server.AddMenuHandler(ID_WEASELTRAY_WIKI,
                          std::bind(open, L"https://rime.im/docs/"));
  m_server.AddMenuHandler(ID_WEASELTRAY_HOMEPAGE,
                          std::bind(open, L"https://rime.im/"));
  m_server.AddMenuHandler(ID_WEASELTRAY_FORUM,
                          std::bind(open, L"https://rime.im/discuss/"));
  m_server.AddMenuHandler(ID_WEASELTRAY_CHECKUPDATE, check_update);
  m_server.AddMenuHandler(ID_WEASELTRAY_INSTALLDIR, std::bind(explore, dir));
  m_server.AddMenuHandler(ID_WEASELTRAY_USERCONFIG,
                          std::bind(explore, WeaselUserDataPath()));
  m_server.AddMenuHandler(ID_WEASELTRAY_LOGDIR,
                          std::bind(explore, WeaselLogPath()));
}
