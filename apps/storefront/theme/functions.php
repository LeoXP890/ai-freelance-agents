<?php
defined('ABSPATH') || exit;
add_action('after_setup_theme', function() {
    add_theme_support('woocommerce'); add_theme_support('post-thumbnails');
    add_theme_support('custom-logo', ['height'=>72, 'width'=>280, 'flex-height'=>true, 'flex-width'=>true]);
    add_theme_support('editor-styles'); add_editor_style('assets/store.css');
});
add_action('wp_enqueue_scripts', function() {
    wp_enqueue_style('aic-curadoria', get_theme_file_uri('assets/store.css'), [], '1.0.0');
    wp_enqueue_script('aic-curadoria', get_theme_file_uri('assets/store.js'), [], '1.0.0', ['strategy'=>'defer']);
});
add_filter('render_block_core/image', function($html,$block) {
    if (str_contains($block['attrs']['url'] ?? $html, 'assets/editorial.svg')) {
        $tag=new WP_HTML_Tag_Processor($html);
        if ($tag->next_tag('IMG')) {
            $tag->set_attribute('width','700');$tag->set_attribute('height','780');
            $tag->set_attribute('fetchpriority','high');$tag->set_attribute('loading','eager');
            return $tag->get_updated_html();
        }
    }
    return $html;
},10,2);
add_action('init', function() { register_block_pattern_category('aic-curadoria', ['label'=>'Curadoria — seções da loja']); });
add_action('init', function() {
    wp_register_script('aic-editor-blocks', get_theme_file_uri('assets/editor.js'), ['wp-blocks','wp-element','wp-server-side-render'], '1.0.0', true);
    foreach (['preview_notice','hero_cta','selection','categories','demo_studies','catalog','product','locked','contact','policy_links','brand_note'] as $name) {
        register_block_type('aic-curadoria/'.str_replace('_','-',$name), [
            'api_version'=>3, 'editor_script'=>'aic-editor-blocks',
            'render_callback'=>fn()=>do_shortcode('[aic_'.$name.']'),
        ]);
    }
});
// WooCommerce auto-inserts account/cart blocks into block-theme headers; they are misleading in preview.
add_filter('hooked_block_types', function($types) {
    return array_values(array_filter($types,fn($type)=>!in_array($type,['woocommerce/mini-cart','woocommerce/customer-account'],true)));
}, 20);
add_action('wp', function() {
    // Replace Woo's fixed overlay with the persistent, unobtrusive preview strip in our header.
    global $wp_filter;
    foreach (($wp_filter['wp_footer']->callbacks ?? []) as $priority=>$callbacks) {
        foreach ($callbacks as $callback) {
            $fn=$callback['function'];
            if (is_array($fn) && is_object($fn[0]) && $fn[0] instanceof Automattic\WooCommerce\Admin\Features\LaunchYourStore && $fn[1]==='maybe_add_coming_soon_banner_on_frontend') remove_action('wp_footer',$fn,$priority);
        }
    }
});
function aic_is_owner(): bool { return current_user_can('manage_options'); }
function aic_url($path=''): string { return esc_url(home_url('/'.$path)); }
function aic_products(bool $demo=false): array {
    if (!function_exists('wc_get_products') || ($demo && !aic_is_owner())) return [];
    $args=['post_type'=>'product','post_status'=>$demo?'draft':'publish','numberposts'=>48,'meta_key'=>$demo?'_aic_demo':'_aic_approved','meta_value'=>'1','orderby'=>'date','order'=>'DESC','fields'=>'ids'];
    return array_values(array_filter(array_map('wc_get_product',get_posts($args))));
}
function aic_empty(string $title, string $text): string {
    return '<div class="empty-state"><span class="empty-mark" aria-hidden="true">↗</span><div><h3>'.esc_html($title).'</h3><p>'.esc_html($text).'</p></div></div>';
}
function aic_cards(array $products, bool $demo=false): string {
    $html='<div class="product-grid">';
    foreach ($products as $i=>$p) {
        if (!$demo && $p->get_meta('_aic_demo')) continue;
        $url=$demo ? get_preview_post_link($p->get_id()) : get_permalink($p->get_id());
        $title=$demo ? preg_replace('/^EXEMPLO — NÃO À VENDA · /u','',$p->get_name()) : $p->get_name();
        $img=$p->get_image_id() ? wp_get_attachment_image($p->get_image_id(),'woocommerce_thumbnail',false,['loading'=>'lazy']) : '<img src="'.esc_url(get_theme_file_uri('assets/study-'.($i%3+1).'.svg')).'" alt="" width="600" height="660" loading="lazy">';
        $html.='<article class="product-card"><a class="product-image" aria-label="Ver detalhes: '.esc_attr($title).'" href="'.esc_url($url).'">'.$img.'<span class="image-arrow" aria-hidden="true">↗</span></a><p class="eyebrow">'.($demo?'Exemplo — não à venda':'Seleção da loja').'</p><h3><a href="'.esc_url($url).'">'.esc_html($title).'</a></h3><p class="product-meta">'.($demo?'Ilustração conceitual · rascunho':wp_kses_post($p->get_price_html())).'</p></article>';
    }
    return $html.'</div>';
}
add_shortcode('aic_preview_notice', function() {
    return aic_is_owner() ? '<div class="preview-strip"><span class="preview-dot" aria-hidden="true"></span> Loja em preparação <span class="preview-detail">· Prévia da administração, sem vendas</span><a href="'.esc_url(admin_url()).'">Painel WordPress ↗</a></div>' : '';
});
add_shortcode('aic_hero_cta', function() {
    return aic_products() ? '<a class="button" href="'.aic_url('produtos/').'">Explorar a seleção <span aria-hidden="true">↗</span></a>' : '<a class="button" href="#curadoria">Conheça a proposta <span aria-hidden="true">↗</span></a>';
});
add_shortcode('aic_selection', function() {
    $ps=aic_products(); return $ps ? aic_cards($ps) : aic_empty('A próxima seleção começa aqui.','Estamos preparando o catálogo. Os produtos aparecerão quando estiverem aprovados — sem antecipar ofertas.');
});
add_shortcode('aic_categories', function() {
    $terms=get_terms(['taxonomy'=>'product_cat','hide_empty'=>true]);
    if (is_wp_error($terms) || !$terms) return '<div class="category-empty"><span>Categorias</span><p>Novos interesses, em breve.<br><small>Disponíveis após a aprovação do catálogo.</small></p><span aria-hidden="true">＋</span></div>';
    $html='<div class="category-list">';
    foreach ($terms as $term) {
        if ($term->slug==='uncategorized' || $term->slug==='sem-categoria') continue;
        $html.='<a href="'.esc_url(add_query_arg('categoria',$term->slug,home_url('/produtos/'))).'">'.esc_html($term->name).' <span aria-hidden="true">↗</span></a>';
    }
    return $html.'</div>';
});
add_shortcode('aic_demo_studies', function() {
    if (!aic_is_owner()) return '';
    $ps=aic_products(true);
    return '<section class="demo-section"><div class="section-heading"><div><p class="eyebrow">Laboratório visual · só administração</p><h2>Um olhar para o que vem.</h2></div><p>Estudos de composição.<br>Exemplos, nunca ofertas.</p></div>'.aic_cards($ps,true).'</section>';
});
add_shortcode('aic_catalog', function() {
    $search=sanitize_text_field(wp_unslash($_GET['busca']??''));
    $cat=sanitize_title(wp_unslash($_GET['categoria']??''));
    $sort=sanitize_key($_GET['ordem']??'recentes');
    $ps=aic_products();
    $ps=array_values(array_filter($ps,function($p)use($search,$cat){return (!$search||mb_stripos($p->get_name(),$search)!==false)&&(!$cat||has_term($cat,'product_cat',$p->get_id()));}));
    if ($sort==='nome') usort($ps,fn($a,$b)=>strnatcasecmp($a->get_name(),$b->get_name()));
    if ($sort==='preco') usort($ps,fn($a,$b)=>(float)$a->get_price()<=>(float)$b->get_price());
    $html='<div class="catalog-intro"><p class="eyebrow">A curadoria</p><h1>Escolhas para<br>o seu cotidiano.</h1><p>Explore o que faz sentido para você. Nosso catálogo está em preparação.</p></div><form class="catalog-filters" action="'.aic_url('produtos/').'" method="get"><div><label for="catalog-search">Buscar no catálogo</label><input id="catalog-search" type="search" name="busca" value="'.esc_attr($search).'" placeholder="O que você procura?"></div><div><label for="category">Categoria</label><select id="category" name="categoria"><option value="">Todas as categorias</option>';
    $terms=get_terms(['taxonomy'=>'product_cat','hide_empty'=>true]);
    foreach (is_wp_error($terms)?[]:$terms as $t) $html.='<option value="'.esc_attr($t->slug).'" '.selected($cat,$t->slug,false).'>'.esc_html($t->name).'</option>';
    $html.='</select></div><div><label for="sort">Ordenar por</label><select id="sort" name="ordem">';
    foreach (['recentes'=>'Mais recentes','nome'=>'Nome','preco'=>'Menor preço'] as $v=>$label) $html.='<option value="'.$v.'" '.selected($sort,$v,false).'>'.$label.'</option>';
    $html.='</select></div><button class="button" type="submit">Aplicar filtros</button></form><p class="results-count" role="status">'.count($ps).' produtos aprovados</p>';
    $html.=$ps?aic_cards($ps):aic_empty($search?'Nenhum resultado para “'.$search.'”.':'Ainda estamos selecionando.',$search?'Tente outro termo ou limpe os filtros.':'Quando o catálogo estiver aprovado, ele aparecerá aqui.');
    if ($search || $cat) $html.='<a class="text-link" href="'.aic_url('produtos/').'">Limpar filtros</a>';
    return $html.do_shortcode('[aic_demo_studies]');
});
add_shortcode('aic_product', function() {
    $p=function_exists('wc_get_product')?wc_get_product(get_the_ID()):false;
    if (!$p) return aic_empty('Produto indisponível.','Volte ao catálogo para consultar os itens disponíveis.');
    $demo=$p->get_meta('_aic_demo')==='1';
    if ($demo && !aic_is_owner()) return '';
    $images=array_filter(array_merge([$p->get_image_id()],$p->get_gallery_image_ids()));
    $visual='';foreach($images as $i=>$id) $visual.=wp_get_attachment_image($id,'large',false,['loading'=>$i?'lazy':'eager']);
    if (!$visual) $visual='<img src="'.esc_url(get_theme_file_uri('assets/study-1.svg')).'" alt="Estudo visual abstrato; não é fotografia de produto" width="600" height="660">';
    $html='<a class="text-link" href="'.aic_url('produtos/').'">← Voltar ao catálogo</a><div class="product-detail"><div class="product-gallery">'.$visual.'</div><div><p class="eyebrow">'.($demo?'EXEMPLO — NÃO À VENDA':'Seleção da loja').'</p><h1>'.esc_html($p->get_name()).'</h1><div class="product-description">'.wp_kses_post(wpautop($p->get_description())).'</div>';
    if (!$demo) {
        $html.='<p class="product-price">'.wp_kses_post($p->get_price_html()).'</p><p>'.esc_html($p->is_in_stock()?'Em estoque':'Indisponível').'</p>';
        if ($p->is_type('variable')) {
            $html.='<dl class="variants">';
            foreach ($p->get_available_variations() as $v) { $html.='<dt>'.esc_html(implode(' / ',array_values($v['attributes']))).'</dt><dd>'.wp_kses_post(wc_price($v['display_price'])).' · '.esc_html($v['is_in_stock']?'Em estoque':'Indisponível').'</dd>'; }
            $html.='</dl>';
        }
    }
    return $html.'<div class="locked-note"><strong>Disponível apenas para revisão.</strong><p>Compras, pagamentos e expedição estão desativados nesta prévia.</p></div></div></div>';
});
add_shortcode('aic_locked', fn()=>aic_empty('Ainda não estamos recebendo pedidos.','Esta loja está em preparação. Carrinho, checkout e contas de clientes ficam indisponíveis até a homologação.'));
add_shortcode('aic_contact', fn()=>'<div class="locked-note"><strong>Atendimento em definição</strong><p>O administrador pode editar esta página no WordPress quando tiver um canal aprovado. Nenhum formulário coleta dados nesta prévia.</p></div>');
add_shortcode('aic_policy_links', function() {
    $html='<ul class="policy-list">';
    foreach (['privacidade'=>'Privacidade','trocas-e-devolucoes'=>'Trocas e devoluções','termos'=>'Termos de uso'] as $slug=>$title) {
        $page=get_page_by_path($slug);
        $html.='<li>'.($page&&$page->post_status==='publish'?'<a href="'.esc_url(get_permalink($page)).'">'.$title.'</a>':esc_html($title).' <small>Em revisão</small>').'</li>';
    }
    return $html.'</ul>';
});
add_shortcode('aic_brand_note', fn()=>'<p class="footer-note">'.esc_html(get_bloginfo('name')).' · Identidade provisória.<br>Identificação comercial e canais de atendimento em definição.</p>');
