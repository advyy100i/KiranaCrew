// KiranaCrew read-only dashboard. Token comes from the bot's /dashboard command (#token=... in the URL).
// API base: --dart-define=API_BASE=http://127.0.0.1:8000 (default: same origin).
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

const apiBase = String.fromEnvironment('API_BASE', defaultValue: '');

void main() => runApp(const KiranaApp());

// ───────────────────────────── data ─────────────────────────────

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
  final r = p.abs() / 100;
  final s = r.toStringAsFixed(r == r.roundToDouble() ? 0 : 2);
  final grouped = s.replaceAllMapped(RegExp(r'(\d)(?=(\d{3})+(?!\d))'), (m) => '${m[1]},');
  return '${p < 0 ? '−' : ''}₹$grouped';
}

String qty(dynamic q) {
  if (q == null) return '';
  final d = double.tryParse(q.toString()) ?? 0;
  return d == d.roundToDouble() ? d.toInt().toString() : d.toStringAsFixed(2);
}

double num_(dynamic v) => double.tryParse((v ?? 0).toString()) ?? 0;

const _months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const _days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

String niceDate(dynamic iso, {bool time = false}) {
  final d = DateTime.tryParse(iso?.toString() ?? '')?.toLocal();
  if (d == null) return '';
  final base = '${d.day} ${_months[d.month - 1]}';
  if (!time) return base;
  final hh = d.hour % 12 == 0 ? 12 : d.hour % 12;
  final mm = d.minute.toString().padLeft(2, '0');
  return '$base, $hh:$mm ${d.hour < 12 ? 'am' : 'pm'}';
}

String longDate(dynamic iso) {
  final d = DateTime.tryParse(iso?.toString() ?? '');
  if (d == null) return '';
  return '${_days[d.weekday - 1]}, ${d.day} ${_months[d.month - 1]} ${d.year}';
}

/// Colour + icon + label for each transaction type.
class TxStyle {
  const TxStyle(this.label, this.icon, this.color);
  final String label;
  final IconData icon;
  final Color color;
}

TxStyle txStyle(String? type, ColorScheme cs) => switch (type) {
      'CASH_SALE' => TxStyle('Cash sale', Icons.payments_outlined, const Color(0xFF2E7D32)),
      'CREDIT_SALE' => TxStyle('Udhaar', Icons.credit_card_outlined, const Color(0xFFE65100)),
      'CREDIT_REPAYMENT' => TxStyle('Wapas', Icons.savings_outlined, const Color(0xFF1565C0)),
      'INVENTORY_PURCHASE' => TxStyle('Stock in', Icons.local_shipping_outlined, const Color(0xFF6A1B9A)),
      'STOCK_ADJUSTMENT' => TxStyle('Adjustment', Icons.tune, const Color(0xFF00695C)),
      'REVERSAL' => TxStyle('Undo', Icons.undo, cs.outline),
      _ => TxStyle(type ?? '', Icons.receipt_long_outlined, cs.outline),
    };

// ───────────────────────────── app shell ─────────────────────────────

class KiranaApp extends StatelessWidget {
  const KiranaApp({super.key});
  @override
  Widget build(BuildContext context) {
    final frag = Uri.base.fragment;
    final token = Uri.splitQueryString(frag.startsWith('token=') ? frag : '')['token'] ?? '';
    ThemeData theme(Brightness b) {
      final cs = ColorScheme.fromSeed(seedColor: const Color(0xFF1B5E20), brightness: b,
          surface: b == Brightness.light ? const Color(0xFFFAF8F3) : const Color(0xFF121412));
      return ThemeData(
        colorScheme: cs,
        useMaterial3: true,
        scaffoldBackgroundColor: cs.surface,
        cardTheme: CardThemeData(
          elevation: 0,
          color: cs.surfaceContainerLowest,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16), side: BorderSide(color: cs.outlineVariant.withValues(alpha: .5))),
          margin: EdgeInsets.zero,
        ),
        dividerTheme: DividerThemeData(color: cs.outlineVariant.withValues(alpha: .4), space: 1),
        navigationBarTheme: NavigationBarThemeData(backgroundColor: cs.surfaceContainerLowest, indicatorColor: cs.primaryContainer),
        navigationRailTheme: NavigationRailThemeData(backgroundColor: cs.surfaceContainerLowest, indicatorColor: cs.primaryContainer),
        inputDecorationTheme: InputDecorationTheme(
          filled: true, fillColor: cs.surfaceContainerLowest, isDense: true,
          border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide(color: cs.outlineVariant)),
          enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide(color: cs.outlineVariant)),
        ),
      );
    }
    return MaterialApp(
      title: 'KiranaCrew',
      debugShowCheckedModeBanner: false,
      theme: theme(Brightness.light),
      darkTheme: theme(Brightness.dark),
      home: token.isEmpty ? const NoToken() : Home(api: Api(token)),
    );
  }
}

class NoToken extends StatelessWidget {
  const NoToken({super.key});
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Scaffold(
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 380),
          child: Card(
            child: Padding(
              padding: const EdgeInsets.all(28),
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                Icon(Icons.storefront, size: 56, color: cs.primary),
                const SizedBox(height: 16),
                Text('KiranaCrew', style: Theme.of(context).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w700)),
                const SizedBox(height: 8),
                Text('Open this page from the bot: send /dashboard in Telegram and tap the link.',
                    textAlign: TextAlign.center, style: TextStyle(color: cs.onSurfaceVariant)),
              ]),
            ),
          ),
        ),
      ),
    );
  }
}

class Home extends StatefulWidget {
  const Home({super.key, required this.api});
  final Api api;
  @override
  State<Home> createState() => _HomeState();
}

class _HomeState extends State<Home> {
  int tab = 0;
  int refreshTick = 0;

  static const _dests = [
    (Icons.today_outlined, Icons.today, 'Aaj'),
    (Icons.people_outline, Icons.people, 'Customers'),
    (Icons.inventory_2_outlined, Icons.inventory_2, 'Stock'),
    (Icons.receipt_long_outlined, Icons.receipt_long, 'Transactions'),
  ];

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final pages = [TodayPage(api: widget.api), CustomersPage(api: widget.api),
                   InventoryPage(api: widget.api), TransactionsPage(api: widget.api)];
    final body = KeyedSubtree(key: ValueKey('$tab-$refreshTick'), child: pages[tab]);
    final title = Row(children: [
      Container(
        padding: const EdgeInsets.all(6),
        decoration: BoxDecoration(color: cs.primary, borderRadius: BorderRadius.circular(8)),
        child: Icon(Icons.storefront, color: cs.onPrimary, size: 18),
      ),
      const SizedBox(width: 10),
      const Text('KiranaCrew', style: TextStyle(fontWeight: FontWeight.w700, letterSpacing: -.3)),
    ]);
    final refresh = IconButton(tooltip: 'Refresh', icon: const Icon(Icons.refresh), onPressed: () => setState(() => refreshTick++));

    return LayoutBuilder(builder: (context, c) {
      final wide = c.maxWidth >= 840;
      if (!wide) {
        return Scaffold(
          appBar: AppBar(title: title, actions: [refresh], backgroundColor: cs.surface, scrolledUnderElevation: 0),
          body: body,
          bottomNavigationBar: NavigationBar(
            selectedIndex: tab,
            onDestinationSelected: (i) => setState(() => tab = i),
            destinations: [for (final d in _dests) NavigationDestination(icon: Icon(d.$1), selectedIcon: Icon(d.$2), label: d.$3)],
          ),
        );
      }
      return Scaffold(
        body: Row(children: [
          NavigationRail(
            extended: c.maxWidth >= 1100,
            selectedIndex: tab,
            onDestinationSelected: (i) => setState(() => tab = i),
            leading: Padding(padding: const EdgeInsets.fromLTRB(8, 12, 8, 20), child: c.maxWidth >= 1100 ? title : title.children.first),
            trailing: Expanded(child: Align(alignment: Alignment.bottomCenter, child: Padding(padding: const EdgeInsets.only(bottom: 16), child: refresh))),
            destinations: [for (final d in _dests) NavigationRailDestination(icon: Icon(d.$1), selectedIcon: Icon(d.$2), label: Text(d.$3))],
          ),
          const VerticalDivider(),
          Expanded(child: Center(child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 1040), child: body))),
        ]),
      );
    });
  }
}

// ───────────────────────────── shared widgets ─────────────────────────────

/// Fetch once, show spinner / error / builder.
class Loader<T> extends StatelessWidget {
  const Loader({super.key, required this.future, required this.builder});
  final Future<T> future;
  final Widget Function(T) builder;
  @override
  Widget build(BuildContext context) => FutureBuilder<T>(
      future: future,
      builder: (c, s) {
        if (s.hasError) {
          return Center(child: Padding(padding: const EdgeInsets.all(24), child: Column(mainAxisSize: MainAxisSize.min, children: [
            Icon(Icons.cloud_off, size: 40, color: Theme.of(c).colorScheme.error),
            const SizedBox(height: 12),
            Text('Could not load', style: Theme.of(c).textTheme.titleMedium),
            const SizedBox(height: 4),
            Text('${s.error}', textAlign: TextAlign.center, style: TextStyle(color: Theme.of(c).colorScheme.onSurfaceVariant)),
          ])));
        }
        if (!s.hasData) return const Center(child: CircularProgressIndicator());
        return builder(s.data as T);
      });
}

class PageTitle extends StatelessWidget {
  const PageTitle(this.title, {super.key, this.subtitle, this.trailing});
  final String title;
  final String? subtitle;
  final Widget? trailing;
  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final cs = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.fromLTRB(4, 8, 4, 16),
      child: Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(title, style: t.headlineSmall?.copyWith(fontWeight: FontWeight.w700, letterSpacing: -.5)),
          if (subtitle != null) Padding(padding: const EdgeInsets.only(top: 2), child: Text(subtitle!, style: t.bodyMedium?.copyWith(color: cs.onSurfaceVariant))),
        ])),
        if (trailing != null) trailing!,
      ]),
    );
  }
}

class SectionLabel extends StatelessWidget {
  const SectionLabel(this.text, {super.key});
  final String text;
  @override
  Widget build(BuildContext context) => Padding(
      padding: const EdgeInsets.fromLTRB(4, 20, 4, 8),
      child: Text(text.toUpperCase(), style: Theme.of(context).textTheme.labelMedium?.copyWith(
          color: Theme.of(context).colorScheme.onSurfaceVariant, letterSpacing: 1.1, fontWeight: FontWeight.w600)));
}

class Empty extends StatelessWidget {
  const Empty(this.icon, this.text, {super.key});
  final IconData icon;
  final String text;
  @override
  Widget build(BuildContext context) => Center(child: Padding(padding: const EdgeInsets.all(40), child: Column(mainAxisSize: MainAxisSize.min, children: [
        Icon(icon, size: 44, color: Theme.of(context).colorScheme.outline),
        const SizedBox(height: 10),
        Text(text, style: TextStyle(color: Theme.of(context).colorScheme.onSurfaceVariant)),
      ])));
}

class Pill extends StatelessWidget {
  const Pill(this.text, {super.key, required this.color, this.icon});
  final String text;
  final Color color;
  final IconData? icon;
  @override
  Widget build(BuildContext context) => Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
      decoration: BoxDecoration(color: color.withValues(alpha: .12), borderRadius: BorderRadius.circular(999)),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        if (icon != null) ...[Icon(icon, size: 13, color: color), const SizedBox(width: 4)],
        Text(text, style: TextStyle(color: color, fontSize: 12, fontWeight: FontWeight.w600)),
      ]));
}

class Avatar extends StatelessWidget {
  const Avatar(this.name, {super.key, this.radius = 20});
  final String name;
  final double radius;
  @override
  Widget build(BuildContext context) {
    const palette = [Color(0xFF1B5E20), Color(0xFF00695C), Color(0xFF1565C0), Color(0xFF6A1B9A), Color(0xFFAD1457), Color(0xFFE65100), Color(0xFF4E342E)];
    final color = palette[name.codeUnits.fold(0, (a, b) => a + b) % palette.length];
    final parts = name.trim().split(RegExp(r'\s+'));
    final initials = parts.take(2).map((p) => p.isEmpty ? '' : p[0].toUpperCase()).join();
    return CircleAvatar(radius: radius, backgroundColor: color.withValues(alpha: .15),
        child: Text(initials, style: TextStyle(color: color, fontWeight: FontWeight.w700, fontSize: radius * .8)));
  }
}

/// Amount coloured by sign: positive = customer owes more (red), negative = paid back (green).
class Money extends StatelessWidget {
  const Money(this.paise, {super.key, this.signed = false, this.size = 15, this.bold = true});
  final dynamic paise;
  final bool signed;
  final double size;
  final bool bold;
  @override
  Widget build(BuildContext context) {
    final v = (paise ?? 0) as num;
    Color? color;
    if (signed) color = v > 0 ? const Color(0xFFC62828) : (v < 0 ? const Color(0xFF2E7D32) : null);
    return Text(inr(v), style: TextStyle(fontSize: size, fontWeight: bold ? FontWeight.w700 : FontWeight.w500, color: color, fontFeatures: const [FontFeature.tabularFigures()]));
  }
}

// ───────────────────────────── Today ─────────────────────────────

class TodayPage extends StatelessWidget {
  const TodayPage({super.key, required this.api});
  final Api api;

  @override
  Widget build(BuildContext context) => Loader(
      future: api.get('/today'),
      builder: (d) {
        final cs = Theme.of(context).colorScheme;
        final t = Theme.of(context).textTheme;
        final low = (d['low_stock'] as List).cast<Map>();
        final sales = (d['sales_paise'] ?? 0) as num;
        final cash = (d['cash_sales_paise'] ?? 0) as num;
        final cashShare = sales > 0 ? cash / sales : 0.0;

        Widget stat(String label, dynamic value, IconData icon, Color color, {String? hint}) => Card(
            child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
                  Row(children: [
                    Container(padding: const EdgeInsets.all(6), decoration: BoxDecoration(color: color.withValues(alpha: .12), borderRadius: BorderRadius.circular(8)),
                        child: Icon(icon, size: 16, color: color)),
                    const SizedBox(width: 8),
                    Expanded(child: Text(label, style: t.labelLarge?.copyWith(color: cs.onSurfaceVariant), overflow: TextOverflow.ellipsis)),
                  ]),
                  const SizedBox(height: 12),
                  Text(value is num ? inr(value) : '$value', style: t.headlineSmall?.copyWith(fontWeight: FontWeight.w700, letterSpacing: -.5, fontFeatures: const [FontFeature.tabularFigures()])),
                  if (hint != null) Text(hint, style: t.bodySmall?.copyWith(color: cs.onSurfaceVariant)),
                ])));

        final hero = Container(
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(16),
            gradient: LinearGradient(begin: Alignment.topLeft, end: Alignment.bottomRight,
                colors: [cs.primary, Color.lerp(cs.primary, Colors.black, .25)!]),
          ),
          child: Padding(
            padding: const EdgeInsets.all(22),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('Aaj ki bikri', style: t.labelLarge?.copyWith(color: cs.onPrimary.withValues(alpha: .8))),
              const SizedBox(height: 6),
              Text(inr(sales), style: t.displaySmall?.copyWith(color: cs.onPrimary, fontWeight: FontWeight.w800, letterSpacing: -1)),
              const SizedBox(height: 16),
              ClipRRect(borderRadius: BorderRadius.circular(6), child: LinearProgressIndicator(value: cashShare, minHeight: 8,
                  backgroundColor: cs.onPrimary.withValues(alpha: .2), color: cs.onPrimary)),
              const SizedBox(height: 8),
              Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
                Text('Cash ${inr(cash)}', style: t.bodySmall?.copyWith(color: cs.onPrimary)),
                Text('Udhaar ${inr(d['credit_given_paise'])}', style: t.bodySmall?.copyWith(color: cs.onPrimary.withValues(alpha: .85))),
              ]),
              const SizedBox(height: 4),
              Text('${d['transactions'] ?? 0} transactions${(d['reversals'] ?? 0) > 0 ? ' · ${d['reversals']} undone' : ''}',
                  style: t.bodySmall?.copyWith(color: cs.onPrimary.withValues(alpha: .7))),
            ]),
          ),
        );

        return ListView(padding: const EdgeInsets.fromLTRB(16, 8, 16, 32), children: [
          PageTitle('Namaste 👋', subtitle: longDate(d['date'])),
          LayoutBuilder(builder: (context, c) {
            final cols = c.maxWidth >= 720 ? 3 : 2;
            final tiles = [
              stat('Udhaar diya', d['credit_given_paise'], Icons.credit_card_outlined, const Color(0xFFE65100)),
              stat('Udhaar wapas', d['credit_collected_paise'], Icons.savings_outlined, const Color(0xFF1565C0)),
              stat('Kul baaki', d['outstanding_paise'], Icons.account_balance_wallet_outlined, const Color(0xFFC62828), hint: 'total outstanding'),
              stat('Kam stock', '${low.length}', Icons.warning_amber_rounded, const Color(0xFFF9A825), hint: low.isEmpty ? 'sab theek' : 'items to reorder'),
            ];
            if (cols == 3) {
              return Column(children: [
                IntrinsicHeight(child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                  Expanded(flex: 3, child: hero),
                  const SizedBox(width: 12),
                  Expanded(flex: 2, child: Column(children: [Expanded(child: tiles[0]), const SizedBox(height: 12), Expanded(child: tiles[1])])),
                ])),
                const SizedBox(height: 12),
                IntrinsicHeight(child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [Expanded(child: tiles[2]), const SizedBox(width: 12), Expanded(child: tiles[3])])),
              ]);
            }
            return Column(children: [
              hero,
              const SizedBox(height: 12),
              GridView.count(crossAxisCount: 2, shrinkWrap: true, physics: const NeverScrollableScrollPhysics(),
                  crossAxisSpacing: 12, mainAxisSpacing: 12, childAspectRatio: 1.45, children: tiles),
            ]);
          }),
          if (d['insight'] != null) ...[
            const SectionLabel('Is hafte ki salah'),
            Card(
              color: cs.tertiaryContainer.withValues(alpha: .35),
              child: Padding(padding: const EdgeInsets.all(16), child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Icon(Icons.lightbulb_outline, color: cs.tertiary),
                const SizedBox(width: 12),
                Expanded(child: Text(d['insight'], style: t.bodyMedium?.copyWith(height: 1.5))),
              ])),
            ),
          ],
          if (low.isNotEmpty) ...[
            const SectionLabel('Kam stock'),
            Card(child: Column(children: [
              for (final (i, r) in low.indexed) ...[
                if (i > 0) const Divider(),
                ListTile(
                  leading: const Icon(Icons.warning_amber_rounded, color: Color(0xFFF9A825)),
                  title: Text(r['name'], style: const TextStyle(fontWeight: FontWeight.w600)),
                  trailing: Pill('${qty(r['stock'])} ${r['base_unit']} left', color: const Color(0xFFE65100)),
                ),
              ],
            ])),
          ],
        ]);
      });
}

// ───────────────────────────── Customers ─────────────────────────────

class CustomersPage extends StatefulWidget {
  const CustomersPage({super.key, required this.api});
  final Api api;
  @override
  State<CustomersPage> createState() => _CustomersPageState();
}

class _CustomersPageState extends State<CustomersPage> {
  late final Future<dynamic> future = widget.api.get('/customers');
  String q = '';

  @override
  Widget build(BuildContext context) => Loader(
      future: future,
      builder: (rows) {
        final cs = Theme.of(context).colorScheme;
        final all = (rows as List).cast<Map>()..sort((a, b) => ((b['balance_paise'] ?? 0) as num).compareTo((a['balance_paise'] ?? 0) as num));
        final list = all.where((r) => r['name'].toString().toLowerCase().contains(q.toLowerCase())).toList();
        final total = all.fold<num>(0, (a, r) => a + (((r['balance_paise'] ?? 0) as num) > 0 ? r['balance_paise'] as num : 0));
        return ListView(padding: const EdgeInsets.fromLTRB(16, 8, 16, 32), children: [
          PageTitle('Customers', subtitle: '${all.length} customers · ${inr(total)} baaki'),
          TextField(
            onChanged: (v) => setState(() => q = v),
            decoration: const InputDecoration(prefixIcon: Icon(Icons.search), hintText: 'Naam dhundo…'),
          ),
          const SizedBox(height: 12),
          if (list.isEmpty) const Empty(Icons.person_search, 'Koi customer nahi mila')
          else Card(child: Column(children: [
            for (final (i, r) in list.indexed) ...[
              if (i > 0) const Divider(),
              ListTile(
                leading: Avatar(r['name']),
                title: Text(r['name'], style: const TextStyle(fontWeight: FontWeight.w600)),
                subtitle: Text(r['last_activity_at'] == null ? 'no activity yet' : 'last ${niceDate(r['last_activity_at'])}',
                    style: TextStyle(color: cs.onSurfaceVariant)),
                trailing: Row(mainAxisSize: MainAxisSize.min, children: [
                  Money(r['balance_paise'], signed: true),
                  const SizedBox(width: 4),
                  Icon(Icons.chevron_right, color: cs.outline),
                ]),
                onTap: () => Navigator.push(context, MaterialPageRoute(
                    builder: (_) => CustomerDetail(api: widget.api, id: r['customer_id'], name: r['name']))),
              ),
            ],
          ])),
        ]);
      });
}

class CustomerDetail extends StatelessWidget {
  const CustomerDetail({super.key, required this.api, required this.id, required this.name});
  final Api api;
  final int id;
  final String name;
  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final t = Theme.of(context).textTheme;
    return Scaffold(
      appBar: AppBar(title: Text(name), backgroundColor: cs.surface, scrolledUnderElevation: 0),
      body: Center(child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 760), child: Loader(
          future: api.get('/customers/$id'),
          builder: (d) {
            final bal = (d['balance_paise'] ?? 0) as num;
            final hist = (d['history'] as List).cast<Map>();
            return ListView(padding: const EdgeInsets.fromLTRB(16, 16, 16, 32), children: [
              Card(child: Padding(padding: const EdgeInsets.all(20), child: Row(children: [
                Avatar(name, radius: 28),
                const SizedBox(width: 16),
                Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(name, style: t.titleLarge?.copyWith(fontWeight: FontWeight.w700)),
                  Text(bal > 0 ? 'Baaki hai' : (bal < 0 ? 'Advance' : 'Hisaab clear'), style: TextStyle(color: cs.onSurfaceVariant)),
                ])),
                Money(bal, signed: true, size: 26),
              ]))),
              const SectionLabel('History'),
              if (hist.isEmpty) const Empty(Icons.history, 'Koi entry nahi')
              else Card(child: Column(children: [
                for (final (i, h) in hist.indexed) ...[
                  if (i > 0) const Divider(),
                  Builder(builder: (context) {
                    final s = txStyle(h['type'], cs);
                    final delta = (h['ledger_delta'] ?? 0) as num;
                    final what = [h['product'], qty(h['quantity']), h['unit']].where((x) => x != null && '$x'.isNotEmpty).join(' ');
                    return ListTile(
                      leading: CircleAvatar(backgroundColor: s.color.withValues(alpha: .12), child: Icon(s.icon, color: s.color, size: 20)),
                      title: Text(what.isEmpty ? s.label : what, style: const TextStyle(fontWeight: FontWeight.w600)),
                      subtitle: Text('${s.label} · ${niceDate(h['created_at'], time: true)}', style: TextStyle(color: cs.onSurfaceVariant)),
                      trailing: Text('${delta > 0 ? '+' : ''}${inr(delta)}', style: TextStyle(fontWeight: FontWeight.w700,
                          color: delta > 0 ? const Color(0xFFC62828) : const Color(0xFF2E7D32), fontFeatures: const [FontFeature.tabularFigures()])),
                    );
                  }),
                ],
              ])),
            ]);
          }))),
    );
  }
}

// ───────────────────────────── Inventory ─────────────────────────────

class InventoryPage extends StatelessWidget {
  const InventoryPage({super.key, required this.api});
  final Api api;
  @override
  Widget build(BuildContext context) => Loader(
      future: api.get('/inventory'),
      builder: (rows) {
        final cs = Theme.of(context).colorScheme;
        final t = Theme.of(context).textTheme;
        final list = (rows as List).cast<Map>();
        bool isLow(Map r) => r['low_stock_threshold'] != null && num_(r['stock']) < num_(r['low_stock_threshold']);
        final sorted = [...list]..sort((a, b) {
          final la = isLow(a) ? 0 : 1, lb = isLow(b) ? 0 : 1;
          return la != lb ? la - lb : a['name'].toString().compareTo(b['name'].toString());
        });
        final lowCount = list.where(isLow).length;

        Widget row(Map r) {
          final low = isLow(r);
          final stock = num_(r['stock']);
          final thr = num_(r['low_stock_threshold']);
          final fc = (r['forecast_7d'] as List? ?? []).cast<Map>();
          final fcSum = fc.fold<double>(0, (a, f) => a + (f['qty'] as num));
          // bar fills relative to 3× the low-stock threshold (or forecast) so "comfortable" reads as ~full
          final ref = thr > 0 ? thr * 3 : (fcSum > 0 ? fcSum * 2 : 0);
          final frac = ref > 0 ? (stock / ref).clamp(0.0, 1.0) : null;
          final barColor = low ? const Color(0xFFE65100) : (frac != null && frac < .5 ? const Color(0xFFF9A825) : const Color(0xFF2E7D32));
          return Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Expanded(child: Text(r['name'], style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15))),
                if (low) ...[const Pill('Low', color: Color(0xFFE65100), icon: Icons.warning_amber_rounded), const SizedBox(width: 8)],
                Text('${qty(r['stock'])} ${r['base_unit']}', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 15,
                    color: low ? const Color(0xFFE65100) : null, fontFeatures: const [FontFeature.tabularFigures()])),
              ]),
              const SizedBox(height: 8),
              if (frac != null)
                ClipRRect(borderRadius: BorderRadius.circular(4), child: LinearProgressIndicator(value: frac, minHeight: 6,
                    backgroundColor: cs.surfaceContainerHighest, color: barColor)),
              const SizedBox(height: 6),
              Row(children: [
                Expanded(child: Text(
                  r['selling_price_paise'] == null ? 'no catalog price' : '${inr(r['selling_price_paise'])} / ${r['base_unit']}'
                      '${thr > 0 ? '  ·  reorder below ${qty(thr)}' : ''}',
                  style: t.bodySmall?.copyWith(color: cs.onSurfaceVariant))),
                if (fc.isNotEmpty)
                  Tooltip(message: '${fc.first['model']} beat the seasonal-naive baseline',
                      child: Pill('~${fcSum.toStringAsFixed(1)} ${r['base_unit']} next 7d', color: const Color(0xFF1565C0), icon: Icons.trending_up)),
              ]),
            ]),
          );
        }

        return ListView(padding: const EdgeInsets.fromLTRB(16, 8, 16, 32), children: [
          PageTitle('Stock', subtitle: '${list.length} items · ${lowCount == 0 ? 'sab theek' : '$lowCount low'}'),
          if (list.isEmpty) const Empty(Icons.inventory_2_outlined, 'Catalog khaali hai')
          else Card(child: Column(children: [
            for (final (i, r) in sorted.indexed) ...[if (i > 0) const Divider(), row(r)],
          ])),
        ]);
      });
}

// ───────────────────────────── Transactions ─────────────────────────────

class TransactionsPage extends StatelessWidget {
  const TransactionsPage({super.key, required this.api});
  final Api api;
  @override
  Widget build(BuildContext context) => Loader(
      future: api.get('/transactions?limit=100'),
      builder: (rows) {
        final cs = Theme.of(context).colorScheme;
        final t = Theme.of(context).textTheme;
        final list = (rows as List).cast<Map>();
        // group by calendar day, preserving API order (newest first)
        final groups = <String, List<Map>>{};
        for (final tx in list) {
          groups.putIfAbsent(niceDate(tx['created_at']), () => []).add(tx);
        }

        Widget tile(Map tx) {
          final s = txStyle(tx['type'], cs);
          final undone = tx['reversed'] == true;
          final what = [tx['product'], qty(tx['quantity']), tx['unit']].where((x) => x != null && '$x'.isNotEmpty).join(' ');
          final who = tx['customer']?.toString();
          return Theme(
            data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
            child: ExpansionTile(
              leading: CircleAvatar(backgroundColor: s.color.withValues(alpha: .12), child: Icon(s.icon, color: s.color, size: 20)),
              title: Row(children: [
                Flexible(child: Text(who ?? (what.isEmpty ? s.label : what),
                    style: TextStyle(fontWeight: FontWeight.w600, decoration: undone ? TextDecoration.lineThrough : null), overflow: TextOverflow.ellipsis)),
                const SizedBox(width: 8),
                Pill(s.label, color: s.color),
                if (undone) ...[const SizedBox(width: 6), Pill('undone', color: cs.outline, icon: Icons.undo)],
              ]),
              subtitle: Text('${who != null && what.isNotEmpty ? '$what · ' : ''}${niceDate(tx['created_at'], time: true)}',
                  style: TextStyle(color: cs.onSurfaceVariant)),
              trailing: tx['amount_paise'] == null ? const SizedBox.shrink() : Money(tx['amount_paise'], size: 15),
              childrenPadding: const EdgeInsets.fromLTRB(72, 0, 16, 14),
              expandedCrossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(color: cs.surfaceContainerLow, borderRadius: BorderRadius.circular(10)),
                  child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Icon(Icons.mic_none, size: 18, color: cs.onSurfaceVariant),
                    const SizedBox(width: 8),
                    Expanded(child: Text(tx['transcript'] ?? '(seed / manual entry)', style: t.bodyMedium?.copyWith(fontStyle: FontStyle.italic, height: 1.4))),
                  ]),
                ),
                const SizedBox(height: 8),
                Wrap(spacing: 6, runSpacing: 6, children: [
                  Pill('#${tx['id']}', color: cs.outline),
                  Pill('parser ${tx['parser_used'] ?? '-'}', color: cs.outline),
                  Pill('stt ${tx['stt_provider'] ?? '-'}${tx['stt_latency_ms'] != null ? ' · ${tx['stt_latency_ms']} ms' : ''}', color: cs.outline),
                  Pill('price ${tx['price_source'] ?? '-'}', color: cs.outline),
                  Pill('${tx['message_status'] ?? '-'}', color: cs.outline),
                ]),
              ],
            ),
          );
        }

        return ListView(padding: const EdgeInsets.fromLTRB(16, 8, 16, 32), children: [
          PageTitle('Transactions', subtitle: 'last ${list.length}'),
          if (list.isEmpty) const Empty(Icons.receipt_long_outlined, 'Abhi tak koi entry nahi'),
          for (final e in groups.entries) ...[
            SectionLabel(e.key),
            Card(child: Column(children: [
              for (final (i, tx) in e.value.indexed) ...[if (i > 0) const Divider(), tile(tx)],
            ])),
          ],
        ]);
      });
}
