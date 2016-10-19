# arubdesu-recipes
Recipes for https://autopkg.github.io/autopkg/

# FAQ

### Why does Microsoft have an update available, but your recipe (doesn't see it yet|fails)?

Sorry, but Microsoft is often faster to provide direct download links than to fix or update the products feed(s). So far, we've had

1. improperly signed packages,
2. no signature at all,
3. broken xml in the feed,
4. xml that points to a valid URL... which 404's,
5. days which turn into weeks which turn into months where they don't update the feed at all.

We usually just have to wait. You can help confirm this symptom by doing the following:

1. Visiting the update feed URL (it's in the URLProvider processor, e.g. [for Lync](https://www.microsoft.com/mac/autoupdate/0409UCCP14.xml))
2. If it's still showing the old URL, we just have to wait, but you can continue...  
#### If you're going above and beyond -
3. Download the new version and install (or unpack the payload from the pkg if you're savvy)
4. Run 'Check for Updates' from the Help menu while doing a packet capture with a tool like [CharlesProxy](https://www.charlesproxy.com)
5. If there's a new update feed, then yes, my recipe is indeed behind the times and needs updating.
 - If not... let's enjoy the wait together!
