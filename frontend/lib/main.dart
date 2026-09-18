// KiranaCrew read-only dashboard. Token comes from the bot's /dashboard command (#token=... in the URL).
// API base: --dart-define=API_BASE=http://127.0.0.1:8000 (default: same origin).
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

const apiBase = String.fromEnvironment('API_BASE', defaultValue: '');

void main() => runApp(const KiranaApp());

class Api {
  Api(this.token);
  final String token;
  Future<dynamic> get(String path) async {
    final r = await http.get(Uri.parse('$apiBase/api/v1/dashboard$path'),
        headers: {'Authorization': 'Bearer $token'});
    if (r.statusCode != 200) throw Exception('${r.statusCode}: ${r.body}');
    return jsonDecode(utf8.decode(r.bodyBytes));
  }
}

String inr(dynamic paise) {
  final p = (paise ?? 0) as num;
  final r = p / 100;
  final s = r.toStringAsFixed(r == r.roundToDouble() ? 0 : 2);
  return '₹${s.replaceAllMapped(RegExp(r'(\d)(?=(\d{3})+(?!\d))'), (m) => '${m[1]},')}';
}

String qty(dynamic q) {
  if (q == null) return '';
  final d = double.tryParse(q.toString()) ?? 0;
  return d == d.roundToDouble() ? d.toInt().toString() : d.toString();
}

class KiranaApp extends StatelessWidget {
  const KiranaApp({super.key});
  @override
  Widget build(BuildContext context) {
    final frag = Uri.base.fragment;
    final token = Uri.splitQueryString(frag.startsWith('token=') ? frag : '')['token'] ?? '';
    return MaterialApp(
      title: 'KiranaCrew',
      theme: ThemeData(colorSchemeSeed: const Color(0xFF2E7D32), useMaterial3: true),
      home: token.isEmpty ? const NoToken() : Home(api: Api(token)),
    );
  }
}

class NoToken extends StatelessWidget {
  const NoToken({super.key});
  @override
  Widget build(BuildContext context) => const Scaffold(
      body: Center(child: Text('Open this page from the bot: send /dashboard in Telegram.')));
}

class Home extends StatefulWidget {
  const Home({super.key, required this.api});
  final Api api;
  @override
  State<Home> createState() => _HomeState();
}

class _HomeState extends State<Home> {
  int tab = 0;
  @override
  Widget build(BuildContext context) {
    final pages = [TodayPage(api: widget.api), CustomersPage(api: widget.api),
                   InventoryPage(api: widget.api), TransactionsPage(api: widget.api)];
    return Scaffold(
      appBar: AppBar(title: const Text('KiranaCrew')),
      body: pages[tab],
      bottomNavigationBar: NavigationBar(
        selectedIndex: tab,
        onDestinationSelected: (i) => setState(() => tab = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.today), label: 'Today'),
          NavigationDestination(icon: Icon(Icons.people), label: 'Customers'),
          NavigationDestination(icon: Icon(Icons.inventory_2), label: 'Inventory'),
          NavigationDestination(icon: Icon(Icons.receipt_long), label: 'Transactions'),
        ],
      ),
    );
  }
}

/// Generic loader: fetch once, show spinner / error / builder.
class Loader<T> extends StatelessWidget {
  const Loader({super.key, required this.future, required this.builder});
  final Future<T> future;
  final Widget Function(T) builder;
  @override
  Widget build(BuildContext context) => FutureBuilder<T>(
      future: future,
      builder: (c, s) {
        if (s.hasError) return Center(child: Text('Error: ${s.error}'));
        if (!s.hasData) return const Center(child: CircularProgressIndicator());
        return builder(s.data as T);
      });
}

class TodayPage extends StatelessWidget {
  const TodayPage({super.key, required this.api});
  final Api api;
  @override
  Widget build(BuildContext context) => Loader(
      future: api.get('/today'),
      builder: (d) {
        final low = (d['low_stock'] as List).cast<Map>();
        Widget tile(String label, String value) => Card(
            child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(label, style: Theme.of(context).textTheme.labelLarge),
                  Text(value, style: Theme.of(context).textTheme.headlineSmall),
                ])));
        return ListView(padding: const EdgeInsets.all(12), children: [
          Text('Aaj: ${d['date']}', style: Theme.of(context).textTheme.titleMedium),
          GridView.count(crossAxisCount: 2, shrinkWrap: true, physics: const NeverScrollableScrollPhysics(),
              childAspectRatio: 2.2, children: [
            tile('Bikri', inr(d['sales_paise'])),
            tile('Cash', inr(d['cash_sales_paise'])),
            tile('Udhaar diya', inr(d['credit_given_paise'])),
            tile('Udhaar wapas', inr(d['credit_collected_paise'])),
            tile('Kul baaki', inr(d['outstanding_paise'])),
            tile('Kam stock', '${low.length} items'),
          ]),
          if (low.isNotEmpty)
            Card(child: ListTile(leading: const Icon(Icons.warning_amber, color: Colors.orange),
                title: const Text('Kam stock'),
                subtitle: Text(low.map((r) => '${r['name']} ${qty(r['stock'])} ${r['base_unit']}').join(', ')))),
          if (d['insight'] != null)
            Card(child: ListTile(leading: const Icon(Icons.lightbulb_outline),
                title: const Text('Is hafte ki salah'), subtitle: Text(d['insight']))),
        ]);
      });
}

class CustomersPage extends StatelessWidget {
  const CustomersPage({super.key, required this.api});
  final Api api;
  @override
  Widget build(BuildContext context) => Loader(
      future: api.get('/customers'),
      builder: (rows) => ListView(
          children: (rows as List).cast<Map>().map((r) => ListTile(
              title: Text(r['name']),
              subtitle: Text(r['last_activity_at'] == null ? '' : 'last: ${r['last_activity_at'].toString().substring(0, 10)}'),
              trailing: Text(inr(r['balance_paise']),
                  style: TextStyle(fontWeight: FontWeight.bold,
                      color: (r['balance_paise'] as num) > 0 ? Colors.red.shade700 : Colors.green.shade700)),
              onTap: () => Navigator.push(context, MaterialPageRoute(
                  builder: (_) => CustomerDetail(api: api, id: r['customer_id'], name: r['name']))))).toList()));
}

class CustomerDetail extends StatelessWidget {
  const CustomerDetail({super.key, required this.api, required this.id, required this.name});
  final Api api;
  final int id;
  final String name;
  @override
  Widget build(BuildContext context) => Scaffold(
      appBar: AppBar(title: Text(name)),
      body: Loader(
          future: api.get('/customers/$id'),
          builder: (d) => ListView(children: [
                ListTile(title: const Text('Balance'), trailing: Text(inr(d['balance_paise']),
                    style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold))),
                const Divider(),
                ...(d['history'] as List).cast<Map>().map((h) => ListTile(
                    dense: true,
                    leading: Icon((h['ledger_delta'] as num) > 0 ? Icons.arrow_upward : Icons.arrow_downward,
                        color: (h['ledger_delta'] as num) > 0 ? Colors.red : Colors.green),
                    title: Text('${h['type']} ${h['product'] ?? ''} ${qty(h['quantity'])} ${h['unit'] ?? ''}'),
                    subtitle: Text(h['created_at'].toString().substring(0, 16)),
                    trailing: Text(inr(h['ledger_delta'])))),
              ])));
}

class InventoryPage extends StatelessWidget {
  const InventoryPage({super.key, required this.api});
  final Api api;
  @override
  Widget build(BuildContext context) => Loader(
      future: api.get('/inventory'),
      builder: (rows) => ListView(
          children: (rows as List).cast<Map>().map((r) {
            final low = r['low_stock_threshold'] != null &&
                double.parse(r['stock'].toString()) < double.parse(r['low_stock_threshold'].toString());
            final fc = (r['forecast_7d'] as List).cast<Map>();
            final fcSum = fc.fold<double>(0, (a, f) => a + (f['qty'] as num));
            return ListTile(
              leading: low ? const Icon(Icons.warning_amber, color: Colors.orange) : const Icon(Icons.check_circle_outline),
              title: Text(r['name']),
              subtitle: Text(fc.isEmpty
                  ? (r['selling_price_paise'] == null ? 'no catalog price' : '${inr(r['selling_price_paise'])}/${r['base_unit']}')
                  : '7-day forecast: ${fcSum.toStringAsFixed(1)} ${r['base_unit']} (${fc.first['model']} beats baseline)'),
              trailing: Text('${qty(r['stock'])} ${r['base_unit']}',
                  style: TextStyle(fontWeight: FontWeight.bold, color: low ? Colors.orange.shade800 : null)),
            );
          }).toList()));
}

class TransactionsPage extends StatelessWidget {
  const TransactionsPage({super.key, required this.api});
  final Api api;
  @override
  Widget build(BuildContext context) => Loader(
      future: api.get('/transactions?limit=100'),
      builder: (rows) => ListView(
          children: (rows as List).cast<Map>().map((t) => ExpansionTile(
                leading: Text('#${t['id']}'),
                title: Text('${t['type']}  ${t['customer'] ?? ''} ${t['product'] ?? ''} ${qty(t['quantity'])} ${t['unit'] ?? ''}'
                    '${t['reversed'] == true ? '  (undone)' : ''}'),
                subtitle: Text(t['created_at'].toString().substring(0, 16)),
                trailing: Text(t['amount_paise'] == null ? '' : inr(t['amount_paise']),
                    style: const TextStyle(fontWeight: FontWeight.bold)),
                children: [
                  ListTile(dense: true, title: const Text('Transcript'), subtitle: Text(t['transcript'] ?? '(seed / manual)')),
                  ListTile(dense: true, title: const Text('Pipeline'),
                      subtitle: Text('parser: ${t['parser_used'] ?? '-'} · stt: ${t['stt_provider'] ?? '-'}'
                          '${t['stt_latency_ms'] != null ? ' (${t['stt_latency_ms']} ms)' : ''} · price: ${t['price_source'] ?? '-'}'
                          ' · status: ${t['message_status'] ?? '-'}')),
                ],
              )).toList()));
}
