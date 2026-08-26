import 'package:flutter/material.dart';
import 'home_screen.dart';
import 'alerts_screen.dart';
import 'scanner_screen.dart';
import 'settings_screen.dart';

void main() {
  runApp(const BulchimbeonApp());
}

class BulchimbeonApp extends StatelessWidget {
  const BulchimbeonApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '불침번',
      theme: ThemeData(
        primarySwatch: Colors.deepOrange,
        useMaterial3: true,
      ),
      home: const MainNavigation(),
    );
  }
}

class MainNavigation extends StatefulWidget {
  const MainNavigation({super.key});

  @override
  State<MainNavigation> createState() => _MainNavigationState();
}

class _MainNavigationState extends State<MainNavigation> {
  int _selectedIndex = 0;

  final List<String> _titles = const ['불침번', '알림', 'QR 스캔', '리포트'];

  // 탭을 누를 때마다 이 함수가 다시 호출되면서 화면이 새로 생성됨
  // (기존처럼 고정 리스트를 쓰면 HomeScreen이 한 번만 만들어지고 재사용되어
  //  initState의 데이터 로딩이 다시 실행되지 않음 — 그래서 탭 이동 후 갱신이 안 됐음)
  Widget _getScreen(int index) {
    switch (index) {
      case 0:
        return const HomeScreen();
      case 1:
        return const AlertsScreen();
      case 2:
        return const ScannerScreen();
      case 3:
        return const SettingsScreen();
      default:
        return const HomeScreen();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(_titles[_selectedIndex]),
        backgroundColor: Colors.deepOrange,
        foregroundColor: Colors.white,
      ),
      body: _getScreen(_selectedIndex),
      bottomNavigationBar: BottomNavigationBar(
        currentIndex: _selectedIndex,
        onTap: (index) {
          setState(() {
            _selectedIndex = index;
          });
        },
        type: BottomNavigationBarType.fixed,
        backgroundColor: Colors.white,
        selectedItemColor: Colors.deepOrange,
        unselectedItemColor: Colors.grey,
        items: const [
          BottomNavigationBarItem(icon: Icon(Icons.home), label: '홈'),
          BottomNavigationBarItem(icon: Icon(Icons.notifications), label: '알림'),
          BottomNavigationBarItem(icon: Icon(Icons.qr_code_scanner), label: 'QR'),
          BottomNavigationBarItem(icon: Icon(Icons.description), label: '리포트'),
        ],
      ),
    );
  }
}